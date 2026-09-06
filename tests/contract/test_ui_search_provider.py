import json
from io import BytesIO

import httpx
import pytest
from PIL import Image, ImageDraw

from letsaigc.assets.resolver import AssetResolver
from letsaigc.assets.search import RoutingPolicy, SearchCriteria, SearchPricing
from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.migrations import migrate_ui_ledger
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import canonical_json
from letsaigc.schemas.ui import UIAnalysisRequest
from letsaigc.schemas.ui_provider import SearchUIInput
from letsaigc.ui_providers.search import SearchAcquisition


@pytest.mark.parametrize("excluded_title", [None, "Game HUD Boons Tier List", "Game HUD 角色强度榜"])
@pytest.mark.parametrize("schema_version", [3, 4, 5])
def test_search_stops_after_first_valid_download_and_reuses_it(tmp_path, monkeypatch, excluded_title, schema_version):
    service = PipelineService(tmp_path, ui_schema=True)
    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=schema_version)

    def put(role, value):
        return service.artifacts.put("acquisition", "plan", canonical_json(value).encode(), role=role)

    prices = {
        provider: put(
            "pricing",
            SearchPricing(
                provider=provider,
                search_upper_usd=0.025,
                search_unit_usd=0.01,
                probe_upper_usd=0,
                probe_actual_usd=0,
                search_basis="https://example.com/search",
                probe_basis="https://example.com/probe",
                terms_version="fixture",
                reviewed_until="2099-01-01",
            ),
        )
        for provider in ("serpapi", "tavily")
    }
    routing = put(
        "routing_policy",
        {
            "routing": RoutingPolicy().model_dump(mode="json"),
            "prices": {name: ref.model_dump(mode="json") for name, ref in prices.items()},
        },
    )
    request = UIAnalysisRequest(
        limits={"downloads_per_query": 1},
        input=SearchUIInput(
            query_ref=put("query", {"queries": ["game HUD"]}),
            criteria_ref=put("criteria", SearchCriteria()),
            routing_policy_ref=routing,
        ),
        budget={
            "max_total_cost_usd": 1,
            "max_iteration_cost_usd": 0.1,
            "max_total_gpu_minutes": 0,
            "max_iteration_gpu_minutes": 0,
            "max_revisions": 2,
        },
    )
    plan = service.ui_plan("acquisition", request)
    service.ledger.consume_approval(approve(service.ledger, plan.task_id, plan.fingerprint))
    image = BytesIO()
    source_image = Image.new("RGB", (160, 240), (70, 100, 130))
    ImageDraw.Draw(source_image).rectangle((20, 20, 140, 50), fill="white")
    source_image.save(image, format="PNG")
    calls = []

    def respond(request):
        calls.append((request.method, request.url.host, request.url.path))
        if request.url.path == "/account.json":
            return httpx.Response(
                200,
                json={
                    "plan_searches_left": 100,
                    "extra_credits": 0,
                    "total_searches_left": 100,
                    "searches_per_month": 1000,
                },
            )
        if request.url.path == "/usage":
            return httpx.Response(
                200, json={"account": {"plan_usage": 0, "plan_limit": 100}, "key": {"usage": 0, "limit": 100}}
            )
        if request.url.path == "/search.json":
            return httpx.Response(
                200,
                json={
                    "search_metadata": {"id": "search-123", "status": "Success"},
                    "images_results": ([{
                        "original": "https://example.com/tier.png",
                        "title": excluded_title,
                        "link": "https://example.com/tier-list",
                    }] if excluded_title else []) + [
                        {
                            "original": "https://example.com/ui.png?signature=private",
                            "title": "Game HUD",
                            "link": "https://example.com/page",
                        }
                    ]
                    * 20,
                },
            )
        return httpx.Response(200, content=image.getvalue(), headers={"content-type": "image/png"})

    transport = httpx.MockTransport(respond)
    resolver = AssetResolver(
        client_factory=lambda: httpx.Client(transport=transport), dns_resolver=lambda host, port: ["93.184.216.34"]
    )
    acquisition = SearchAcquisition(
        service,
        transports={name: transport for name in prices},
        credentials={name: "fixture-key" for name in prices},
        resolver=resolver,
    )
    acquisition.acquire(plan)
    result = acquisition.result(plan)
    before = len(calls)
    acquisition.acquire(plan)
    assert len(calls) == before
    assert result.status == "ready" and len(result.sources) == 1
    assert sum(path == "/search.json" for _, _, path in calls) == 1
    assert sum(path == "/ui.png" for _, _, path in calls) == 1
    assert sum(path == "/tier.png" for _, _, path in calls) == 0
    if excluded_title:
        from letsaigc.schemas.pipeline import ArtifactRef

        search = next(op for op in service.ledger.list_operations(plan.task_id) if op.step_id == "search.attempt.1")
        index_ref = ArtifactRef.model_validate(search.result["candidate_index_ref"])
        candidates = json.loads(service.artifacts.read(index_ref))
        assert candidates[0]["metadata_relevant"] is False
        assert candidates[0]["metadata_rejection_reason"] == "non_ui_tier_list"
    assert service.artifacts.read(result.sources[0].original_ref) == image.getvalue()
    provenance = json.loads(service.artifacts.read(result.sources[0].provenance_ref))
    assert provenance["search_backend"] == "serpapi" and provenance["license_status"] == "unknown"
    assert "signature" not in json.dumps(provenance)
    assert service.ledger.usage(plan.task_id)["actual"].cost_usd == 0.01
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM quota_probes WHERE state!='settled'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM quota_reservations WHERE state!='settled'").fetchone()[0] == 0
    from temporalio import activity

    from letsaigc.execution.temporal.ui_activities import UIActivities
    from letsaigc.execution.temporal.ui_messages import UIActivityInput
    from letsaigc.schemas.pipeline import ArtifactRef
    from letsaigc.ui_analysis.normalize import normalize

    monkeypatch.setattr(activity, "heartbeat", lambda *args: None)
    activities = UIActivities(service)
    for phase in ("provide", "normalize"):
        argument = UIActivityInput(task_id=plan.task_id, plan_fingerprint=plan.fingerprint, phase=phase)
        operation = activities.submit(argument)
        argument = argument.model_copy(update={"operation_id": operation.operation_id})
        activities.observe(argument)
        operation = activities.collect(argument)
    canonical_ref = next(
        ArtifactRef.model_validate(ref) for ref in operation.result["artifacts"] if ref["role"] == "canonical"
    )
    direct, _ = normalize(
        service.artifacts, result.sources[0].original_ref, "same-manual-normalizer", request.resources
    )
    assert canonical_ref.sha256 == direct.canonical_ref.sha256
    assert len(calls) == before


def test_persistent_search_dedup_is_visual_and_does_not_force_landscape():
    from letsaigc.ui_providers.search import perceptual_hash

    image = Image.new("RGB", (160, 240), "blue")
    assert perceptual_hash(image) == perceptual_hash(image.copy())
    assert len(perceptual_hash(image)) == 16
