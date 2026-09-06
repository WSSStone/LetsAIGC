"""Offline review-to-selection CLI behavior, without model or approval calls."""

import json

import pytest
from PIL import Image
from typer.testing import CliRunner

from letsaigc.cli import app
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef
from letsaigc.schemas.ui import UIAnalysisRequest
from letsaigc.schemas.ui_review import ReviewConfirm, ReviewDocument, ReviewPatch
from letsaigc.ui_analysis import cli, runtime
from letsaigc.ui_analysis.normalize import png
from letsaigc.ui_analysis.review import ReviewRepository


@pytest.fixture
def reviewed(tmp_path, monkeypatch):
    service = PipelineService(tmp_path / "state", ui_schema=4)
    service.smoke_plan("reviewed-source")
    repo = ReviewRepository(service.ledger, service.artifacts)
    image = service.artifacts.put(
        "reviewed-source", "input", png(Image.new("RGB", (20, 20), "red")),
        role="canonical", media_type="image/png",
    )
    repo.initialize(ReviewDocument(
        task_id="reviewed-source", source_id="source-1", width=20, height=20,
        elements=[
            {"element_id": "icon", "base_type": "image", "semantic_tags": ["icon"], "bbox": [1, 1, 8, 8]},
            {"element_id": "label", "base_type": "text", "bbox": [1, 10, 18, 18]},
        ],
    ), {"canonical_ref": image.model_dump(mode="json")})
    repo.confirm("reviewed-source", ReviewConfirm(request_id="confirm", draft_revision=0))
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({
        "max_total_cost_usd": 1, "max_iteration_cost_usd": 1,
        "max_total_gpu_minutes": 1, "max_iteration_gpu_minutes": 1, "max_revisions": 2,
    }))
    monkeypatch.setattr(cli, "service", lambda: service)
    monkeypatch.setattr(runtime, "freeze_models", lambda *a, **k: pytest.fail("review must not require OCR/VLM"))
    monkeypatch.setattr(cli, "approve", lambda *a, **k: pytest.fail("editing preparation cannot approve"))
    monkeypatch.setattr(cli, "start_approved", lambda *a, **k: pytest.fail("editing execution is not wired yet"))
    return service, repo, budget


def invoke(*args):
    result = CliRunner().invoke(app, ["--json", "ui", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_confirmed_plan_freezes_latest_and_survives_later_confirmation(reviewed):
    service, repo, budget = reviewed
    planned = invoke("plan", "--reviewed-task", "reviewed-source", "--mode", "decompose", "--budget", str(budget))
    assert planned["reviewed"]["review_revision"] == 0
    frozen = service.ledger.plan(planned["task_id"])
    request = UIAnalysisRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(frozen.parameters["request_ref"]))
    )
    assert "ocr" not in request.model_bindings and "vlm" not in request.model_bindings
    document = ReviewDocument.model_validate_json(service.artifacts.read(request.model_bindings["layout_ref"]))
    assert document.task_id == planned["task_id"]
    repo.save("reviewed-source", ReviewPatch(
        request_id="new", base_confirmed_revision=0,
        actions=[{"action": "update_box", "element_id": "icon", "bbox": [2, 2, 9, 9]}],
    ))
    repo.confirm("reviewed-source", ReviewConfirm(
        request_id="confirm-new", draft_revision=1, expected_confirmed_revision=0,
    ))
    assert service.checked_plan(frozen.task_id, frozen.fingerprint).fingerprint == frozen.fingerprint
    assert document.elements[0].bbox == [1, 1, 8, 8]


def test_plan_select_reopen_inspect_and_repeated_selection_are_local(reviewed):
    service, _, budget = reviewed
    planned = invoke("plan", "--reviewed-task", "reviewed-source", "--mode", "decompose", "--budget", str(budget))
    candidate = planned["selection"]["candidates"][0]
    preview = ArtifactRef.model_validate(candidate["preview_ref"])
    assert preview.media_type == "image/png" and service.artifacts.read(preview).startswith(b"\x89PNG")
    selected = invoke("select", planned["task_id"], "--candidate", candidate["candidate_id"])
    repeated = invoke("select", planned["task_id"], "--candidate", candidate["candidate_id"])
    assert repeated["selection_ref"] == selected["selection_ref"]
    assert repeated["selection_revision"] == selected["selection_revision"]
    inspected = invoke("inspect", planned["task_id"], "--local")
    assert inspected["selection"]["selection_ref"] == selected["selection_ref"]
    assert inspected["plan_fingerprint"] == planned["plan_fingerprint"]
    assert inspected["pending_approvals"] == []
    assert service.ledger.list_operations(planned["task_id"]) == []
    denied = CliRunner().invoke(app, [
        "--json", "ui", "execute", planned["task_id"], "--approve", planned["plan_fingerprint"],
    ])
    assert denied.exit_code == 3
    assert json.loads(denied.stdout)["error"]["code"] == "capability_not_ready"


def test_reviewed_input_and_file_override_are_mutually_exclusive(reviewed):
    _, _, budget = reviewed
    result = CliRunner().invoke(app, [
        "--json", "ui", "plan", "--reviewed-task", "reviewed-source", "--mode", "decompose",
        "--selection", "missing.json", "--budget", str(budget),
    ])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "invalid_selection"
