from concurrent.futures import ThreadPoolExecutor

import pytest

from letsaigc.assets.search import QuotaObservation, RoutingPolicy, SearchPricing
from letsaigc.assets.search_routing import QuotaLedger
from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.migrations import migrate_ui_ledger
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import Cost
from letsaigc.schemas.ui import UIAnalysisRequest, UIStepBinding


@pytest.fixture
def quota_service(tmp_path):
    service = PipelineService(tmp_path, ui_schema=True)
    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=3)
    return service


def make_task(service, name):
    from letsaigc.schemas.ui_provider import SearchUIInput

    refs = [service.artifacts.put(name, "plan", b"{}", role=role) for role in ("query", "criteria", "routing_policy")]
    request = UIAnalysisRequest(
        input=SearchUIInput(query_ref=refs[0], criteria_ref=refs[1], routing_policy_ref=refs[2]),
        budget={
            "max_total_cost_usd": 1,
            "max_iteration_cost_usd": 0.1,
            "max_total_gpu_minutes": 0,
            "max_iteration_gpu_minutes": 0,
            "max_revisions": 2,
        },
    )
    plan = service.ui_plan(name, request)
    service.ledger.consume_approval(approve(service.ledger, name, plan.fingerprint))
    return plan, request, UIStepBinding(task_id=name, step_id="search.attempt.1", capability="ui.search", inputs=refs)


def pricing():
    return SearchPricing(
        provider="serpapi",
        search_upper_usd=0.025,
        search_unit_usd=0.025,
        search_basis="https://serpapi.com/pricing",
        probe_upper_usd=0,
        probe_actual_usd=0,
        probe_basis="https://serpapi.com/account-api",
        terms_version="test",
        reviewed_until="2099-01-01",
    )


def seed(service, remaining=1):
    quotas = QuotaLedger(service.ledger)
    scope = quotas.scope("serpapi", "primary", "synthetic-key")
    quotas.snapshot(
        QuotaObservation(
            provider="serpapi",
            scope_id=scope,
            unit="search",
            status="known",
            remaining=remaining,
            total_limit=100,
            observed_at=1000,
        )
    )
    return quotas, scope


def reserve(quotas, values, scope, now=1010):
    plan, request, binding = values
    return quotas.reserve_search(
        plan,
        request,
        binding,
        logical_query_id="query-1",
        policy=RoutingPolicy(),
        scopes={"serpapi": scope},
        prices={"serpapi": pricing()},
        now=now,
    )


def test_last_account_credit_is_atomic_across_tasks(quota_service):
    quotas, scope = seed(quota_service)
    tasks = [make_task(quota_service, f"search-{index}") for index in range(2)]

    def attempt(values):
        try:
            return reserve(quotas, values, scope)[0].state
        except PipelineError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, tasks))
    assert sorted(outcomes) == ["prepared", "search_unavailable"]
    with quota_service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM quota_routes").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1


def test_fresh_snapshot_does_not_assume_local_debit_is_covered(quota_service):
    quotas, scope = seed(quota_service)
    operation, route = reserve(quotas, make_task(quota_service, "first"), scope)
    quota_service.ledger.begin_submit(operation.operation_id)
    quota_service.ledger.finish_ui(operation.operation_id, Cost(cost_usd=0.025), {"quota_units": 1})
    quotas.snapshot(
        QuotaObservation(
            provider="serpapi",
            scope_id=scope,
            unit="search",
            status="known",
            remaining=1,
            total_limit=100,
            observed_at=1080,
        )
    )
    with pytest.raises(PipelineError, match="search_unavailable"):
        reserve(quotas, make_task(quota_service, "second"), scope, now=1090)
    assert quotas.route(operation.operation_id) == route


def test_budget_rejection_rolls_back_route_and_quota_reservation(quota_service):
    quotas, scope = seed(quota_service)
    plan, request, binding = make_task(quota_service, "budget")
    expensive = pricing().model_copy(update={"search_upper_usd": 0.5})
    with pytest.raises(PipelineError):
        quotas.reserve_search(
            plan,
            request,
            binding,
            logical_query_id="query-1",
            policy=RoutingPolicy(),
            scopes={"serpapi": scope},
            prices={"serpapi": expensive},
            now=1010,
        )
    with quota_service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM quota_reservations").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


def test_probe_coalescing_refresh_and_task_limit_survive_reopen(quota_service):
    quotas, scope = seed(quota_service, 100)
    plan, request, binding = make_task(quota_service, "probe")
    first = binding.model_copy(update={"step_id": "search.probe.serpapi.1"})
    op = quotas.reserve_probe(
        plan, request, first, provider="serpapi", scope_id=scope, price=pricing(), policy=RoutingPolicy(), now=1400
    )
    other_plan, other_request, other_binding = make_task(quota_service, "other")
    with pytest.raises(PipelineError, match="probe_in_progress"):
        QuotaLedger(quota_service.ledger).reserve_probe(
            other_plan,
            other_request,
            other_binding.model_copy(update={"step_id": "search.probe.serpapi.1"}),
            provider="serpapi",
            scope_id=scope,
            price=pricing(),
            policy=RoutingPolicy(),
            now=1500,
        )
    quota_service.ledger.begin_submit(op.operation_id)
    quota_service.ledger.finish_ui(op.operation_id, Cost(), {})
    with pytest.raises(PipelineError, match="probe_rate_limited"):
        quotas.reserve_probe(
            plan,
            request,
            first.model_copy(update={"step_id": "search.probe.serpapi.2"}),
            provider="serpapi",
            scope_id=scope,
            price=pricing(),
            policy=RoutingPolicy(),
            now=1410,
        )
    second = quotas.reserve_probe(
        plan,
        request,
        first.model_copy(update={"step_id": "search.probe.serpapi.2"}),
        provider="serpapi",
        scope_id=scope,
        price=pricing(),
        policy=RoutingPolicy(),
        now=1500,
    )
    quota_service.ledger.begin_submit(second.operation_id)
    quota_service.ledger.finish_ui(second.operation_id, Cost(), {})
    with pytest.raises(PipelineError, match="probe_limit"):
        quotas.reserve_probe(
            plan,
            request,
            first.model_copy(update={"step_id": "search.probe.serpapi.3"}),
            provider="serpapi",
            scope_id=scope,
            price=pricing(),
            policy=RoutingPolicy(),
            now=1600,
        )


def test_key_rotation_invalidates_snapshot_but_preserves_account_scope(quota_service):
    quotas, scope = seed(quota_service)
    assert quotas.scope("serpapi", "primary", "rotated-synthetic-key") == scope
    assert quotas.available({"serpapi": scope}, now=1010) == {}
