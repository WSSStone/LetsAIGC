"""T031 sequential batch contracts using isolated v5 ledgers and fake models."""

import json

import pytest
from PIL import Image
from test_ui_analysis_workflow import ModelBackend

from letsaigc.execution.temporal.projection import project
from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.contracts import Capability, Submission
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.migrations import migrate_ui_ledger
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import Cost, PipelineRun, canonical_json
from letsaigc.schemas.ui import UIAnalysisRequest, UIObservation
from letsaigc.schemas.ui_provider import SearchUIInput, UIInputManifest, UIProvisionResult
from letsaigc.ui_analysis.execution import PHASES, UIExecution
from letsaigc.ui_providers.intake import UIIntake


def batch_class():
    from letsaigc.ui_analysis.batch import BatchExecution

    return BatchExecution


def family(
    tmp_path,
    *,
    count=2,
    policy="continue_independent",
    duplicate=False,
    total=1,
    source="manual",
    request_overrides=None,
):
    service = PipelineService(tmp_path / "service", ui_schema=True)
    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    paths = []
    for index in range(count):
        path = tmp_path / f"input-{index}.png"
        Image.new("RGB", (48, 32), (40 + index, 70, 90)).save(path)
        paths.append(str(path))
    intake = UIIntake(service.artifacts)
    imported = intake.import_images([paths[0], paths[0], *paths[1:]] if duplicate else paths)
    bound = intake.rebind(imported, "batch-contract", allowed_scopes={imported.task_id})
    if source == "search":
        manifest = UIInputManifest.model_validate_json(service.artifacts.read(bound.metadata_ref))
        sources = []
        for original in manifest.sources:
            provenance = json.loads(service.artifacts.read(original.provenance_ref))
            provenance.update(origin="https", acquisition_method="search", search_backend="serpapi")
            provenance["source_page"] = "https://example.com/game"
            ref = service.artifacts.put(
                "batch-contract",
                "acquired",
                canonical_json(provenance).encode(),
                role="provenance",
                source_ids=[original.provenance_ref.artifact_id],
            )
            sources.append(original.model_copy(update={"provenance_ref": ref}))
        manifest = manifest.model_copy(update={"sources": sources})
        index = service.artifacts.put(
            "batch-contract",
            "acquired",
            canonical_json(manifest).encode(),
            role="input_manifest",
        )
        supplied = UIProvisionResult(
            provider_id="search",
            provider_version="offline",
            status="ready",
            sources=sources,
            index_ref=index,
        )

        class FrozenSearchBackend:
            capability = Capability(id="ui.search", idempotent_submission=True)
            calls = 0

            def submit(self, operation_id, arguments):
                self.calls += 1
                return Submission(request_id=operation_id)

            def inspect(self, submission):
                return UIObservation(state="succeeded", actual=Cost())

            def collect(self, submission):
                return [("sources", canonical_json(supplied).encode(), "application/json")]

        service.backends["ui.search"] = FrozenSearchBackend()

        def put(role, value):
            return service.artifacts.put("batch-contract", "plan", canonical_json(value).encode(), role=role)

        bound = SearchUIInput(
            query_ref=put("query", {"queries": ["game HUD"]}),
            criteria_ref=put("criteria", {}),
            routing_policy_ref=put("routing_policy", {}),
            max_images=count,
        )
    request = UIAnalysisRequest(
        input=bound,
        batch_failure_policy=policy,
        budget={
            "max_total_cost_usd": total,
            "max_iteration_cost_usd": 0.25,
            "max_total_gpu_minutes": 0,
            "max_iteration_gpu_minutes": 0,
            "max_revisions": 2,
        },
    )
    if request_overrides:
        request = UIAnalysisRequest.model_validate(request.model_dump(mode="json") | request_overrides)
    plan = service.ui_plan("batch-contract", request)
    service.ledger.consume_approval(approve(service.ledger, plan.task_id, plan.fingerprint))
    for name in ("ocr", "analyze"):
        service.backends["ui." + name] = ModelBackend("ui." + name, service.artifacts)
    return service, plan, batch_class()(service)


def run_child(service, child):
    runner = UIExecution(service, evidence_kind="offline")
    for phase in PHASES:
        prepared = runner.prepare(child, phase, 0)
        operation = service.submit_step(child, prepared.binding, prepared.reservation)
        service.observe_step(child, operation.operation_id)
        service.collect_step(child, operation.operation_id)
    result = PipelineRun(
        task_id=child.task_id,
        workflow_id=child.task_id,
        temporal_run_id="offline-child",
        plan_fingerprint=child.fingerprint,
        state="succeeded",
        artifacts=runner.outputs(child),
        projection_sequence=1,
    )
    project(service, result)
    return result


def test_batch_preparation_is_replay_stable_and_has_one_active_child(tmp_path):
    service, plan, batch = family(tmp_path)
    assert plan.workflow_type == "ui_batch"
    first = batch.prepare(plan)
    assert first.child.workflow_type == "ui_analysis"
    child_request = UIExecution(service).request(first.child)
    assert child_request.input.kind == "manual"
    assert len(child_request.input.inputs) == 1
    assert all(ref.task_id == first.child.task_id for ref in child_request.references())
    assert batch_class()(service).prepare(plan).child == first.child
    assert len(service.ledger.list_operations(plan.task_id)) == 1
    with service.ledger.transaction() as db:
        children = db.execute(
            "SELECT task_id,parent_task_id,root_task_id,purpose FROM ui_child_bindings WHERE root_task_id=?",
            (plan.task_id,),
        ).fetchall()
    assert [tuple(row) for row in children] == [(first.child.task_id, plan.task_id, plan.task_id, "analysis")]


@pytest.mark.parametrize("source", ["manual", "search"])
def test_batch_completes_real_children_sequentially_without_repeating_models(tmp_path, source):
    service, plan, batch = family(tmp_path, source=source)
    first = batch.prepare(plan).child
    first_result = run_child(service, first)
    batch.complete(plan, first_result)
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM ui_child_bindings").fetchone()[0] == 1
    second = batch.prepare(plan).child
    assert second.task_id != first.task_id
    batch.complete(plan, run_child(service, second))
    final = batch.prepare(plan)
    assert final.child is None and final.state == "succeeded"
    assert len(final.entries) == 2
    assert {entry["status"] for entry in final.entries} == {"succeeded"}
    assert service.backends["ui.ocr"].calls == service.backends["ui.analyze"].calls == 2
    assert batch_class()(service).prepare(plan).state == "succeeded"
    assert len(service.ledger.list_operations(plan.task_id)) == 1


def test_batch_rejects_unrecorded_child_success(tmp_path):
    _, plan, batch = family(tmp_path)
    child = batch.prepare(plan).child
    forged = PipelineRun(
        task_id=child.task_id,
        workflow_id=child.task_id,
        temporal_run_id="forged",
        plan_fingerprint=child.fingerprint,
        state="succeeded",
    )
    with pytest.raises(PipelineError):
        batch.complete(plan, forged)
    assert batch.prepare(plan).child.task_id == child.task_id


def test_continue_as_new_checkpoint_refuses_active_child_and_preserves_finished_cursor(tmp_path):
    service, plan, batch = family(tmp_path)
    child = batch.prepare(plan).child
    with pytest.raises(PipelineError) as caught:
        batch.checkpoint(plan)
    assert caught.value.code == "active_child"
    batch.complete(plan, run_child(service, child))
    checkpoint = batch.checkpoint(plan)
    assert checkpoint is not None
    next_child = batch_class()(service).prepare(plan).child
    assert next_child.task_id != child.task_id
    assert service.backends["ui.analyze"].calls == 1


def test_ten_original_entries_allowed_but_eleventh_duplicate_rejected_before_dedup(tmp_path):
    service, plan, batch = family(tmp_path, count=10)
    assert len(batch.prepare(plan).entries) == 10
    with pytest.raises(PipelineError) as caught:
        UIIntake(service.artifacts).import_images([str(tmp_path / "input-0.png")] * 11)
    assert caught.value.code == "input_count"


def test_exact_duplicates_share_child_but_near_identical_images_are_independent(tmp_path):
    service, plan, batch = family(tmp_path, duplicate=True)
    first = batch.prepare(plan).child
    batch.complete(plan, run_child(service, first))
    second = batch.prepare(plan).child
    assert second.task_id != first.task_id
    batch.complete(plan, run_child(service, second))
    entries = batch.prepare(plan).entries
    assert [entry["entry_id"] for entry in entries] == ["input-1", "input-2", "input-3"]
    assert entries[1]["duplicate_of"] == "input-1"
    assert entries[0]["source_id"] != entries[1]["source_id"]
    assert entries[0]["child_task_id"] == entries[1]["child_task_id"] == first.task_id
    assert entries[2]["child_task_id"] == second.task_id
    assert service.backends["ui.ocr"].calls == service.backends["ui.analyze"].calls == 2


def child_projection(service, child, state, *, reason=None):
    run = PipelineRun(
        task_id=child.task_id,
        workflow_id=child.task_id,
        temporal_run_id="offline-boundary",
        plan_fingerprint=child.fingerprint,
        state=state,
        stop_reason=reason,
        projection_sequence=1,
    )
    project(service, run)
    return run


def start_analysis(service, child):
    runner = UIExecution(service, evidence_kind="offline")
    for phase in ("provide", "normalize", "ocr"):
        item = runner.prepare(child, phase, 0)
        operation = service.submit_step(child, item.binding, item.reservation)
        service.observe_step(child, operation.operation_id)
        service.collect_step(child, operation.operation_id)
    return runner.prepare(child, "analyze", 0)


@pytest.mark.parametrize("policy", ["continue_independent", "stop_on_error"])
@pytest.mark.parametrize("source", ["manual", "search"])
def test_failed_child_follows_policy_without_discarding_input_mapping(tmp_path, policy, source):
    service, plan, batch = family(tmp_path, policy=policy, source=source)
    child = batch.prepare(plan).child
    item = start_analysis(service, child)
    operation = service.submit_step(child, item.binding, item.reservation)
    service.ledger.finish(operation.operation_id, Cost(), {"error_code": "model_failed"}, failed=True)
    batch.complete(plan, child_projection(service, child, "failed", reason="model_failed"))
    next_item = batch.prepare(plan)
    assert next_item.entries[0]["status"] == "failed"
    assert len(next_item.entries) == 2
    if policy == "continue_independent":
        assert next_item.child.task_id != child.task_id
        batch.complete(plan, run_child(service, next_item.child))
        assert batch.prepare(plan).state == "partial"
    else:
        assert next_item.child is None
        assert next_item.state in {"failed", "partial"}
        assert next_item.entries[1]["status"] == "unprocessed"


@pytest.mark.parametrize(
    "state,reason",
    [
        ("awaiting_approval", "awaiting_selection"),
        ("awaiting_approval", "awaiting_approval"),
        ("awaiting_approval", "budget_insufficient"),
        ("awaiting_reconciliation", "outcome_unknown"),
    ],
)
def test_waiting_child_blocks_later_inputs_even_with_continue_independent(tmp_path, state, reason):
    service, plan, batch = family(tmp_path)
    child = batch.prepare(plan).child
    child_projection(service, child, state, reason=reason)
    status = batch.status(plan)
    assert status.state in {state, reason}
    assert batch.prepare(plan).child.task_id == child.task_id
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM ui_child_bindings").fetchone()[0] == 1
    assert service.backends["ui.analyze"].calls == 0


@pytest.mark.parametrize("reason", ["budget_insufficient", "approval_required", "outcome_unknown"])
def test_failed_projection_cannot_reclassify_a_gate_as_an_independent_failure(tmp_path, reason):
    service, plan, batch = family(tmp_path)
    child = batch.prepare(plan).child
    failure = child_projection(service, child, "failed", reason=reason)
    try:
        batch.complete(plan, failure)
        prepared = batch.prepare(plan)
    except PipelineError as exc:
        assert exc.code in {reason, "awaiting_approval", "awaiting_reconciliation", "invalid_child_result"}
    else:
        assert prepared.child is None or prepared.child.task_id == child.task_id
        assert prepared.entries[1]["status"] == "unprocessed"
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM ui_child_bindings").fetchone()[0] == 1


@pytest.mark.parametrize("source", ["manual", "search"])
def test_parent_cancel_closes_root_before_prepared_child_can_submit(tmp_path, source):
    service, plan, batch = family(tmp_path, source=source)
    child = batch.prepare(plan).child
    prepared = start_analysis(service, child)
    operation = service.ledger.reserve(
        child,
        prepared.binding.step_id,
        0,
        prepared.reservation,
        ui_binding=prepared.binding,
        ui_request=UIExecution(service).request(child),
    )
    batch.cancel(plan)
    with service.ledger.transaction() as db:
        assert (
            db.execute(
                "SELECT submission_gate FROM ui_budget_groups WHERE root_task_id=?",
                (plan.task_id,),
            ).fetchone()[0]
            != "open"
        )
    with pytest.raises(PipelineError):
        service.ledger.begin_submit(operation.operation_id)
    assert service.backends["ui.analyze"].calls == 0
    assert batch.prepare(plan).child is None


@pytest.mark.parametrize("source", ["manual", "search"])
def test_parent_cancel_preserves_unknown_child_reservation_and_cannot_finish(tmp_path, source):
    service, plan, batch = family(tmp_path, source=source)

    class UnknownBackend(ModelBackend):
        def inspect(self, submission):
            return UIObservation(state="unknown", actual=None)

    service.backends["ui.analyze"] = UnknownBackend("ui.analyze", service.artifacts)
    child = batch.prepare(plan).child
    prepared = start_analysis(service, child)
    operation = service.submit_step(child, prepared.binding, prepared.reservation)
    service.ledger.uncertain(operation.operation_id)
    before = service.ledger.get(operation.operation_id)
    assert batch.cancel(plan) is False
    after = service.ledger.get(operation.operation_id)
    assert after.state == "outcome_unknown"
    assert after.reserved == before.reserved == Cost(cost_usd=0.25)
    assert after.provider_request_id == before.provider_request_id
    assert service.ledger.usage(plan.task_id, include_children=True)["unsettled"] == before.reserved
    assert batch.status(plan).state == "awaiting_reconciliation"
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM ui_child_bindings").fetchone()[0] == 1


@pytest.mark.parametrize("source", ["manual", "search"])
def test_child_costs_aggregate_to_root_and_remaining_budget_cannot_reset(tmp_path, source):
    service, plan, batch = family(tmp_path, total=0.3, source=source)

    class PricedBackend(ModelBackend):
        def inspect(self, submission):
            return UIObservation(state="succeeded", actual=Cost(cost_usd=0.2))

    service.backends["ui.analyze"] = PricedBackend("ui.analyze", service.artifacts)
    first = batch.prepare(plan).child
    batch.complete(plan, run_child(service, first))
    assert service.ledger.usage(plan.task_id, include_children=True)["actual"] == Cost(cost_usd=0.2)
    second = batch.prepare(plan).child
    item = start_analysis(service, second)
    with pytest.raises(PipelineError) as caught:
        service.submit_step(second, item.binding, item.reservation)
    assert caught.value.code in {"budget_insufficient", "total_budget"}
    assert service.backends["ui.analyze"].calls == 1


def test_search_parent_supplies_once_and_manual_children_preserve_search_provenance(tmp_path):
    service, plan, batch = family(tmp_path, source="search")
    for _ in range(2):
        child = batch.prepare(plan).child
        request = UIExecution(service).request(child)
        assert request.input.kind == "manual"
        assert "ui.search" not in child.envelope.allowed_capabilities
        manifest = UIInputManifest.model_validate_json(service.artifacts.read(request.input.metadata_ref))
        provenance = json.loads(service.artifacts.read(manifest.sources[0].provenance_ref))
        assert provenance["acquisition_method"] == "search"
        assert provenance["search_backend"] == "serpapi"
        assert provenance["source_page"] == "https://example.com/game"
        batch.complete(plan, run_child(service, child))
    assert batch.prepare(plan).state == "succeeded"
    assert service.backends["ui.search"].calls == 1
    assert service.backends["ui.analyze"].calls == 2


def test_batch_bound_selection_is_split_to_each_image_scope(tmp_path):
    service, original, _ = family(tmp_path)
    request = UIExecution(service).request(original)
    manifest = UIInputManifest.model_validate_json(service.artifacts.read(request.input.metadata_ref))
    intake = UIIntake(service.artifacts)
    inputs = intake.rebind(request.input.metadata_ref, "selected-batch", allowed_scopes={original.task_id})
    selection = service.artifacts.put(
        "selected-batch",
        "input",
        canonical_json(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "source_id": source.source_id,
                        "original_sha256": source.original_ref.sha256,
                        "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 20, 20]}],
                    }
                    for source in manifest.sources
                ],
            }
        ).encode(),
        role="selection",
    )
    selected = UIAnalysisRequest.model_validate(
        request.model_dump(mode="json")
        | {
            "input": inputs.model_dump(mode="json"),
            "output_mode": "decompose",
            "selection_mode": "bound",
            "selection_ref": selection.model_dump(mode="json"),
        }
    )
    plan = service.ui_plan("selected-batch", selected)
    service.ledger.consume_approval(approve(service.ledger, plan.task_id, plan.fingerprint))
    batch = batch_class()(service)
    first = batch.prepare(plan).child
    bound = UIExecution(service).request(first).selection_ref
    value = json.loads(service.artifacts.read(bound))
    assert bound.task_id == first.task_id
    assert [item["source_id"] for item in value["sources"]] == [manifest.sources[0].source_id]


def test_batch_status_exposes_terminal_parent_stop_without_advancing_child(tmp_path):
    service, root, batch = family(tmp_path)
    child = batch.prepare(root).child
    child_projection(service, root, "failed", reason="budget_insufficient")
    status = batch.status(root)
    assert status.state == "failed"
    assert status.reason == "budget_insufficient"
    assert status.child.task_id == child.task_id
    assert batch.prepare(root).child.task_id == child.task_id


def test_batch_rejects_reusing_one_frozen_layout_for_multiple_images(tmp_path):
    from letsaigc.pipelines.registry import validate_ui_registration

    service, root, _ = family(tmp_path)
    request = UIExecution(service).request(root)
    ref = service.artifacts.put(root.task_id, "frozen", b"{}", role="layout")
    request = request.model_copy(update={"model_bindings": {"layout_ref": ref}})
    request_ref = service.artifacts.put(root.task_id, "plan", canonical_json(request).encode(), role="request")
    plan = root.model_copy(
        update={"parameters": root.parameters | {"request_ref": request_ref.model_dump(mode="json")}}
    )
    with pytest.raises(PipelineError) as caught:
        validate_ui_registration(plan, service.artifacts)
    assert caught.value.code == "source_scope"


def test_unknown_supply_remains_inspectable_and_blocks_new_children(tmp_path):
    service, root, batch = family(tmp_path, source="search")
    service.backends["ui.search"].inspect = lambda _: UIObservation(state="unknown", actual=None)
    with pytest.raises(PipelineError):
        batch.prepare(root)
    operations = service.ledger.list_operations(root.task_id)
    assert any(item.state == "outcome_unknown" for item in operations)
    snapshot = batch.status(root)
    assert snapshot.state == "awaiting_reconciliation"
    assert snapshot.reason == "outcome_unknown"
    assert snapshot.child is None
    assert batch.prepare(root).state == "awaiting_reconciliation"
    assert service.backends["ui.search"].calls == 1
