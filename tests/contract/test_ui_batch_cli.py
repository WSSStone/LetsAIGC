"""T031: batch routing and offline CLI boundaries; no provider execution.

T033 must satisfy these tests before batch CLI capability is accepted.
"""

import json

import pytest
from PIL import Image
from typer.testing import CliRunner

from letsaigc.cli import app
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef
from letsaigc.schemas.ui import UIAnalysisRequest
from letsaigc.ui_analysis import cli


@pytest.fixture
def batch_cli(tmp_path, monkeypatch):
    service = PipelineService(tmp_path / "service", ui_schema=5)
    monkeypatch.setattr(cli, "service", lambda: service)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    image = tmp_path / "image.png"
    Image.new("RGB", (48, 32), (40, 70, 90)).save(image)
    budget = tmp_path / "budget.yaml"
    budget.write_text(
        "max_total_cost_usd: 1\nmax_iteration_cost_usd: 0.25\n"
        "max_total_gpu_minutes: 0\nmax_iteration_gpu_minutes: 0\nmax_revisions: 2\n"
    )
    runner = CliRunner()

    def invoke(*arguments):
        return runner.invoke(app, ["--json", "ui", *map(str, arguments)])

    def imported(count, *, invalid_last=False):
        paths = [image] * count
        if invalid_last:
            paths[-1] = tmp_path / "missing.png"
        result = invoke("import", *(arg for path in paths for arg in ("--image", path)))
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        path = tmp_path / "manifest-ref.json"
        path.write_text(json.dumps(payload["input_manifest_ref"]))
        return path, payload

    return service, invoke, imported, budget, image


@pytest.mark.parametrize("count,workflow_type", [(1, "ui_analysis"), (2, "ui_batch"), (10, "ui_batch")])
def test_manual_route_uses_original_count_even_when_every_image_is_identical(batch_cli, count, workflow_type):
    service, invoke, imported, budget, _ = batch_cli
    reference, manifest = imported(count)
    assert manifest["input_count"] == count
    result = invoke("plan", "--input-manifest", reference, "--budget", budget)
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    plan = service.ledger.plan(payload["task_id"])
    assert plan.workflow_type == workflow_type
    assert len(payload["plan_fingerprint"]) == 64
    assert service.ledger.list_operations(plan.task_id) == []
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_one_good_one_failed_input_is_still_a_batch(batch_cli):
    service, invoke, imported, budget, _ = batch_cli
    reference, manifest = imported(2, invalid_last=True)
    assert [entry["status"] for entry in manifest["entries"]] == ["ready", "failed"]
    result = invoke("plan", "--input-manifest", reference, "--budget", budget)
    assert result.exit_code == 0, result.stdout
    assert service.ledger.plan(json.loads(result.stdout)["task_id"]).workflow_type == "ui_batch"


@pytest.mark.parametrize("maximum,workflow_type", [(1, "ui_analysis"), (2, "ui_batch"), (10, "ui_batch")])
def test_search_route_and_maximum_are_frozen_without_search_or_probe(batch_cli, maximum, workflow_type):
    service, invoke, _, budget, _ = batch_cli
    result = invoke("plan", "--query", "game HUD", "--max-images", maximum, "--budget", budget)
    assert result.exit_code == 0, result.stdout
    plan = service.ledger.plan(json.loads(result.stdout)["task_id"])
    request = UIAnalysisRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(plan.parameters["request_ref"]))
    )
    assert (plan.workflow_type, request.input.max_images) == (workflow_type, maximum)
    assert service.ledger.list_operations(plan.task_id) == []
    assert "game HUD" not in plan.model_dump_json()


def test_eleventh_manual_entry_is_rejected_before_deduplication_or_planning(batch_cli):
    service, invoke, _, _, image = batch_cli
    result = invoke("import", *(arg for _ in range(11) for arg in ("--image", image)))
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "input_count"
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_search_eleventh_image_is_rejected_before_task_registration(batch_cli):
    service, invoke, _, budget, _ = batch_cli
    result = invoke("plan", "--query", "game HUD", "--max-images", 11, "--budget", budget)
    assert result.exit_code != 0
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.parametrize("count", [1, 2])
def test_manual_input_rejects_search_only_max_images(batch_cli, count):
    service, invoke, imported, budget, _ = batch_cli
    reference, _ = imported(count)
    result = invoke("plan", "--input-manifest", reference, "--max-images", 1, "--budget", budget)
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "invalid_input"
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.parametrize("policy", ["continue_independent", "stop_on_error"])
def test_failure_policy_is_frozen_in_plan(batch_cli, policy):
    service, invoke, imported, budget, _ = batch_cli
    reference, _ = imported(2)
    result = invoke("plan", "--input-manifest", reference, "--budget", budget,
                    "--batch-failure-policy", policy)
    assert result.exit_code == 0, result.stdout
    plan = service.ledger.plan(json.loads(result.stdout)["task_id"])
    request = UIAnalysisRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(plan.parameters["request_ref"]))
    )
    assert request.batch_failure_policy == policy


def test_search_batch_preflight_keeps_frozen_maximum(batch_cli, monkeypatch):
    from letsaigc.ui_analysis import runtime

    service, invoke, _, budget, _ = batch_cli
    planned = invoke("plan", "--query", "game HUD", "--max-images", 2, "--budget", budget)
    assert planned.exit_code == 0, planned.stdout
    plan = service.ledger.plan(json.loads(planned.stdout)["task_id"])
    monkeypatch.setattr(runtime, "diagnose", lambda **kwargs: {
        "ocr": {"ready": True, "signing_configured": True}, "vlm": {"ready": True},
        "temporal": {"ready": True}, "search": {"ready": True}, "resources": {"free_disk_bytes": 10**12},
    })
    runtime.preflight(service, plan)
    assert service.ledger.list_operations(plan.task_id) == []
