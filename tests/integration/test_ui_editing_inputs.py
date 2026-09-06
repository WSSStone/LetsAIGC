"""Reuse actual parse artifacts through the offline editing CLI."""

import json

import pytest
from PIL import Image
from test_ui_analysis_workflow import ModelBackend
from typer.testing import CliRunner

from letsaigc.cli import app
from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef, PipelineRun
from letsaigc.schemas.ui import UIAnalysisRequest
from letsaigc.ui_analysis import cli, runtime
from letsaigc.ui_analysis.execution import UIExecution
from letsaigc.ui_providers.intake import UIIntake


def test_completed_automatic_parse_can_be_frozen_and_selected_without_new_model_calls(tmp_path, monkeypatch):
    service = PipelineService(tmp_path / "state", ui_schema=4)
    image = tmp_path / "original.jpg"
    Image.new("RGB", (48, 32), "red").save(image)
    intake = UIIntake(service.artifacts)
    imported = intake.import_images([str(image)])
    budget = {
        "max_total_cost_usd": 1, "max_iteration_cost_usd": 0.25,
        "max_total_gpu_minutes": 0, "max_iteration_gpu_minutes": 0, "max_revisions": 2,
    }
    original = service.ui_plan("parse-source", UIAnalysisRequest(
        input=intake.rebind(imported, "parse-source", allowed_scopes={imported.task_id}), budget=budget,
    ))
    service.ledger.consume_approval(approve(service.ledger, original.task_id, original.fingerprint))
    ocr, vlm = (ModelBackend(name, service.artifacts) for name in ("ui.ocr", "ui.analyze"))
    service.backends.update({"ui.ocr": ocr, "ui.analyze": vlm})
    runner = UIExecution(service, evidence_kind="offline")
    for phase in ("provide", "normalize", "ocr", "analyze", "layout", "crop", "project"):
        step = runner.prepare(original, phase, 0)
        op = service.submit_step(original, step.binding, step.reservation)
        service.observe_step(original, op.operation_id)
        service.collect_step(original, op.operation_id)
    run = PipelineRun(
        task_id=original.task_id, plan_fingerprint=original.fingerprint, state="succeeded",
        workflow_id=original.task_id, temporal_run_id="test-run",
        artifacts=runner.outputs(original),
    )
    with service.ledger.transaction() as db:
        db.execute("INSERT INTO projections(task_id,sequence,payload) VALUES(?,?,?)", (
            original.task_id, 1, run.model_dump_json(),
        ))
    budget_file = tmp_path / "budget.json"
    budget_file.write_text(json.dumps(budget))
    monkeypatch.setattr(cli, "service", lambda: service)
    monkeypatch.setattr(runtime, "freeze_models", lambda *a, **k: pytest.fail("Existing layout must be reused"))
    result = CliRunner().invoke(app, [
        "--json", "ui", "plan", "--automatic-task", original.task_id,
        "--mode", "decompose", "--budget", str(budget_file),
    ])
    assert result.exit_code == 0, result.stdout
    planned = json.loads(result.stdout)
    assert planned["automatic"]["layout_origin"] == "automatic" and planned["reviewed"] is None
    candidate = planned["selection"]["candidates"][0]
    selected = CliRunner().invoke(app, [
        "--json", "ui", "select", planned["task_id"], "--candidate", candidate["candidate_id"],
    ])
    assert selected.exit_code == 0, selected.stdout
    request = UIAnalysisRequest.model_validate_json(service.artifacts.read(ArtifactRef.model_validate(
        service.ledger.plan(planned["task_id"]).parameters["request_ref"],
    )))
    assert request.input.inputs[0].sha256 != request.model_bindings["canonical_ref"].sha256
    assert request.input.inputs[0].task_id == planned["task_id"]
    assert ocr.calls == vlm.calls == 1
    assert service.ledger.list_operations(planned["task_id"]) == []
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM ui_review_heads").fetchone()[0] == 0
