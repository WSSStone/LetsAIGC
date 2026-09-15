"""T033: batch result mapping and provenance are derived from actual children."""

import json

from typer.testing import CliRunner

from letsaigc.cli import app
from letsaigc.schemas.pipeline import ArtifactRef
from letsaigc.ui_analysis import cli


def test_batch_inspect_and_manifest_preserve_every_input_and_shared_usage(tmp_path, monkeypatch):
    # Import the integration helper explicitly: all model calls stay synthetic.
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "integration"))
    from test_ui_batch import family, run_child

    service, plan, batch = family(tmp_path, duplicate=True)
    monkeypatch.setattr(cli, "service", lambda: service)
    for _ in range(2):
        child = batch.prepare(plan).child
        batch.complete(plan, run_child(service, child))
    output = CliRunner().invoke(app, ["--json", "ui", "inspect", plan.task_id, "--local"])
    assert output.exit_code == 0, output.stdout
    payload = json.loads(output.stdout)
    assert payload["status"] == "succeeded"
    assert len(payload["entries"]) == 3
    assert len(payload["children"]) == 2
    assert payload["entries"][1]["duplicate_of"] == "input-1"
    assert payload["entries"][0]["child_task_id"] == payload["entries"][1]["child_task_id"]
    assert payload["root_budget_id"] == plan.task_id
    ref = ArtifactRef.model_validate(batch.manifest(plan))
    manifest = json.loads(service.artifacts.read(ref))
    assert len(manifest["entries"]) == 3
    assert len(manifest["children"]) == 2
    assert manifest["quality_status"] == "pending"
    assert manifest["production_export_approved"] is False
    assert all(item["artifacts"] for item in manifest["children"])
    assert str(tmp_path) not in service.artifacts.read(ref).decode()


def test_stop_on_error_processes_inputs_before_the_failed_entry(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "integration"))
    from PIL import Image
    from test_ui_analysis_workflow import ModelBackend
    from test_ui_batch import run_child

    from letsaigc.pipelines.approval import approve
    from letsaigc.pipelines.service import PipelineService
    from letsaigc.schemas.ui import UIAnalysisRequest
    from letsaigc.ui_analysis.batch import BatchExecution
    from letsaigc.ui_providers.intake import UIIntake

    service = PipelineService(tmp_path / "service", ui_schema=5)
    paths = [tmp_path / "first.png", tmp_path / "missing.png", tmp_path / "last.png"]
    Image.new("RGB", (48, 32), "red").save(paths[0])
    Image.new("RGB", (48, 32), "blue").save(paths[2])
    intake = UIIntake(service.artifacts)
    ref = intake.import_images([str(path) for path in paths])
    plan = service.ui_plan(
        "ordered-errors",
        UIAnalysisRequest(
            input=intake.rebind(ref, "ordered-errors", allowed_scopes={ref.task_id}),
            batch_failure_policy="stop_on_error",
            budget={
                "max_total_cost_usd": 1,
                "max_iteration_cost_usd": 0.25,
                "max_total_gpu_minutes": 0,
                "max_iteration_gpu_minutes": 0,
                "max_revisions": 2,
            },
        ),
    )
    service.ledger.consume_approval(approve(service.ledger, plan.task_id, plan.fingerprint))
    for name in ("ocr", "analyze"):
        service.backends["ui." + name] = ModelBackend("ui." + name, service.artifacts)
    batch = BatchExecution(service)
    first = batch.prepare(plan)
    assert first.child is not None
    batch.complete(plan, run_child(service, first.child))
    final = batch.prepare(plan)
    assert final.child is None and final.state == "partial"
    assert batch.status(plan).state == "partial"
    assert [item["status"] for item in final.entries] == ["succeeded", "failed", "unprocessed"]
