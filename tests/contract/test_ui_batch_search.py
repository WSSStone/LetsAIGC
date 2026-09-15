"""Bounded batch acquisition with real providers/ledger and offline HTTP fixtures."""

import json
import random
from io import BytesIO

import httpx
import pytest
from PIL import Image

from letsaigc.assets.resolver import AssetResolver
from letsaigc.assets.search import RoutingPolicy, SearchCriteria, SearchPricing
from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.errors import OutcomeUnknown, PipelineError
from letsaigc.pipelines.migrations import migrate_ui_ledger
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import canonical_json
from letsaigc.schemas.ui import UIAnalysisRequest
from letsaigc.schemas.ui_provider import SearchUIInput
from letsaigc.ui_providers.search import SearchAcquisition


def png(seed, *, compress_level=6):
    # Independent textured images have distinct visual hashes; encoding changes
    # preserve the pixels for the perceptual-duplicate case.
    pixels = random.Random(seed).randbytes(32 * 32 * 3)
    image = Image.frombytes("RGB", (32, 32), pixels).resize((160, 160))
    output = BytesIO()
    image.save(output, format="PNG", compress_level=compress_level)
    return output.getvalue()


def acquisition_case(tmp_path, *, maximum=2, queries=None, rows=None, limits=None, images=None, tavily_rows=None):
    service = PipelineService(tmp_path, ui_schema=True)
    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)

    def put(role, value):
        return service.artifacts.put("batch-search", "plan", canonical_json(value).encode(), role=role)

    providers = ["serpapi", "tavily"] if tavily_rows is not None else ["serpapi"]
    prices = {provider: put("pricing", SearchPricing(
        provider=provider, search_upper_usd=0.025, search_unit_usd=0.01,
        probe_upper_usd=0, probe_actual_usd=0, search_basis="https://example.com/search",
        probe_basis="https://example.com/probe", terms_version="fixture", reviewed_until="2099-01-01",
    )) for provider in providers}
    routing = put("routing_policy", {
        "routing": RoutingPolicy(allowed_providers=providers).model_dump(mode="json"),
        "prices": {provider: price.model_dump(mode="json") for provider, price in prices.items()},
    })
    request = UIAnalysisRequest(
        input=SearchUIInput(
            query_ref=put("query", {"queries": queries or ["game HUD"]}),
            criteria_ref=put("criteria", SearchCriteria()), routing_policy_ref=routing, max_images=maximum,
        ),
        limits=limits or {},
        budget={"max_total_cost_usd": 1, "max_iteration_cost_usd": 0.1,
                "max_total_gpu_minutes": 0, "max_iteration_gpu_minutes": 0, "max_revisions": 2},
    )
    plan = service.ui_plan("batch-search", request)
    service.ledger.consume_approval(approve(service.ledger, plan.task_id, plan.fingerprint))
    calls = []
    images = images or {f"/{index}.png": png(index) for index in range(12)}

    def respond(request):
        calls.append((request.url.path, request.url.params.get("q")))
        if request.url.path == "/account.json":
            return httpx.Response(200, json={
                "plan_searches_left": 100, "extra_credits": 0,
                "total_searches_left": 100, "searches_per_month": 1000,
            })
        if request.url.path == "/usage":
            return httpx.Response(200, json={
                "account": {"plan_usage": 0, "plan_limit": 100}, "key": {"usage": 0, "limit": 100},
            })
        if request.url.path == "/search":
            return httpx.Response(200, json={
                "request_id": "tavily-fixture", "usage": {"credits": 1},
                "images": [{"url": "https://example.com" + path, "description": "Game HUD"}
                           for path in tavily_rows],
            })
        if request.url.path == "/search.json":
            entries = (rows or {}).get(request.url.params["q"], list(images))
            if entries is None:
                raise httpx.ReadTimeout("offline timeout")
            return httpx.Response(200, json={
                "search_metadata": {"id": "search-fixture", "status": "Success"},
                "images_results": [{"original": "https://example.com" + path,
                                    "title": "Game HUD", "link": "https://example.com/page"}
                                   for path in entries],
            })
        return httpx.Response(200, content=images.get(request.url.path, b"invalid"),
                              headers={"content-type": "image/png"})

    transport = httpx.MockTransport(respond)
    resolver = AssetResolver(client_factory=lambda: httpx.Client(transport=transport),
                             dns_resolver=lambda host, port: ["93.184.216.34"])
    acquisition = SearchAcquisition(service, transports={provider: transport for provider in providers},
                                    credentials={provider: "fixture-key" for provider in providers}, resolver=resolver)
    return service, plan, acquisition, calls


def test_batch_stops_at_frozen_selection_limit_and_reuses_sources(tmp_path):
    service, plan, acquisition, calls = acquisition_case(tmp_path)
    acquisition.acquire(plan)
    result = acquisition.result(plan)
    assert result.status == "ready" and len(result.sources) == 2
    assert {service.artifacts.read(source.original_ref) for source in result.sources} == {png(0), png(1)}
    assert calls == [("/account.json", None), ("/search.json", "game HUD"), ("/0.png", None), ("/1.png", None)]
    before = list(calls)
    acquisition.acquire(plan)
    assert calls == before
    assert service.ledger.usage(plan.task_id)["actual"].cost_usd == 0.01


@pytest.mark.parametrize("limits,expected", [({"downloads_per_query": 3}, 3), ({"candidates_per_query": 2}, 2)])
def test_supply_below_maximum_is_ready_and_obeys_query_limits(tmp_path, limits, expected):
    service, plan, acquisition, calls = acquisition_case(tmp_path, maximum=10, limits=limits)
    acquisition.acquire(plan)
    result = acquisition.result(plan)
    assert result.status == "ready" and result.errors_ref is None
    assert len(result.sources) == expected
    assert sum(path.endswith(".png") for path, _ in calls) == expected
    before = list(calls)
    acquisition.acquire(plan)
    assert calls == before
    assert service.ledger.usage(plan.task_id)["actual"].cost_usd == 0.01


def test_batch_uses_root_three_attempt_budget_across_queries(tmp_path):
    service, plan, acquisition, calls = acquisition_case(
        tmp_path, maximum=10, queries=["game HUD 1", "game HUD 2", "game HUD 3"],
        rows={"game HUD 1": ["/0.png"], "game HUD 2": ["/1.png"], "game HUD 3": ["/2.png"]},
    )
    acquisition.acquire(plan)
    result = acquisition.result(plan)
    assert result.status == "ready" and len(result.sources) == 3
    assert [query for path, query in calls if path == "/search.json"] == ["game HUD 1", "game HUD 2", "game HUD 3"]
    assert service.ledger.usage(plan.task_id)["actual"].cost_usd == 0.03
    acquisition.acquire(plan)
    assert sum(path == "/search.json" for path, _ in calls) == 3


@pytest.mark.parametrize("limits", [{"downloads_per_query": 3}, {"candidates_per_query": 3}])
def test_provider_switch_keeps_query_candidate_and_download_totals(tmp_path, limits):
    service, plan, acquisition, calls = acquisition_case(
        tmp_path, maximum=10, limits=limits, rows={"game HUD": ["/0.png", "/1.png"]},
        tavily_rows=["/2.png", "/3.png", "/4.png"],
    )
    acquisition.acquire(plan)
    result = acquisition.result(plan)
    assert result.status == "ready" and len(result.sources) == 3
    assert [path for path, _ in calls if path.endswith(".png")] == ["/0.png", "/1.png", "/2.png"]
    summary = json.loads(service.artifacts.read(result.index_ref))
    assert summary["counters"]["search_attempts"] == 2
    assert service.ledger.usage(plan.task_id)["actual"].cost_usd == 0.02


def test_full_query_download_budget_prevents_unusable_provider_switch(tmp_path):
    _, plan, acquisition, calls = acquisition_case(tmp_path, maximum=10, tavily_rows=["/6.png"])
    acquisition.acquire(plan)
    assert len(acquisition.result(plan).sources) == 5
    assert not any(path == "/search" for path, _ in calls)


@pytest.mark.parametrize("duplicate", [png(0), png(0, compress_level=0)], ids=["exact", "perceptual"])
def test_batch_deduplicates_across_queries_and_keeps_failure_evidence(tmp_path, duplicate):
    service, plan, acquisition, calls = acquisition_case(
        tmp_path, maximum=3, queries=["game HUD 1", "game HUD 2"],
        rows={"game HUD 1": ["/0.png"], "game HUD 2": ["/duplicate.png", "/1.png"]},
        images={"/0.png": png(0), "/duplicate.png": duplicate, "/1.png": png(1)},
    )
    acquisition.acquire(plan)
    result = acquisition.result(plan)
    assert len(result.sources) == 2 and len({source.source_id for source in result.sources}) == 2
    assert result.status == "partial" and result.errors_ref is not None
    errors = json.loads(service.artifacts.read(result.errors_ref))
    assert "duplicate_candidate" in json.dumps(errors)
    assert sum(path.endswith(".png") for path, _ in calls) == 3


def test_failed_candidate_and_success_are_structured_partial_supply(tmp_path):
    service, plan, acquisition, _ = acquisition_case(
        tmp_path, rows={"game HUD": ["/invalid.png", "/0.png"]}, images={"/0.png": png(0)},
    )
    acquisition.acquire(plan)
    result = acquisition.result(plan)
    assert result.status == "partial" and len(result.sources) == 1
    errors = json.loads(service.artifacts.read(result.errors_ref))
    assert errors[0]["operation_id"] and errors[0]["error_code"]
    summary = json.loads(service.artifacts.read(result.index_ref))
    assert summary["failures"] == errors


def test_unknown_second_query_blocks_retry_even_with_acquired_source(tmp_path):
    service, plan, acquisition, calls = acquisition_case(
        tmp_path, queries=["game HUD 1", "game HUD 2"],
        rows={"game HUD 1": ["/0.png"], "game HUD 2": None},
    )
    with pytest.raises(OutcomeUnknown):
        acquisition.acquire(plan)
    assert len(acquisition.existing_sources(plan)) == 1
    before = list(calls)
    with pytest.raises(OutcomeUnknown):
        acquisition.acquire(plan)
    assert calls == before
    assert any(op.state == "outcome_unknown" for op in service.ledger.list_operations(plan.task_id))


def test_lost_candidate_urls_are_not_permission_to_search_again(tmp_path, monkeypatch):
    _, plan, acquisition, calls = acquisition_case(tmp_path)

    def interrupted(*args):
        raise RuntimeError("offline worker interruption")

    monkeypatch.setattr(acquisition, "download", interrupted)
    with pytest.raises(RuntimeError, match="worker interruption"):
        acquisition.acquire(plan)
    before = list(calls)
    with pytest.raises(PipelineError) as caught:
        acquisition.acquire(plan)
    assert caught.value.code == "input_resupply_required"
    assert calls == before
