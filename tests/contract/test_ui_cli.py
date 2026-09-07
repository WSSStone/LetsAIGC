import json

import pytest
from typer.testing import CliRunner

from letsaigc.cli import app
from letsaigc.pipelines.migrations import migrate_ui_ledger
from letsaigc.pipelines.service import PipelineService


def test_cli_import_plan_and_exact_fingerprint_without_network(tmp_path, ui_fixture_dir, monkeypatch):
    from letsaigc.ui_analysis import cli

    service = PipelineService(tmp_path / "service")
    migrate_ui_ledger(service.ledger.path, writers_stopped=True)
    monkeypatch.setattr(cli, "service", lambda: service)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    runner = CliRunner()
    imported = runner.invoke(app, ["--json", "ui", "import", "--image", str(ui_fixture_dir / "hud-portrait.png")])
    assert imported.exit_code == 0, imported.stdout
    ref_file = tmp_path / "input-ref.json"
    ref_file.write_text(json.dumps(json.loads(imported.stdout)["input_manifest_ref"]))
    budget = tmp_path / "budget.yaml"
    budget.write_text(
        "max_total_cost_usd: 1\nmax_iteration_cost_usd: 0.25\n"
        "max_total_gpu_minutes: 0\nmax_iteration_gpu_minutes: 0\nmax_revisions: 2\n"
    )
    planned = runner.invoke(app, ["--json", "ui", "plan", "--input-manifest", str(ref_file), "--budget", str(budget)])
    assert planned.exit_code == 0, planned.stdout
    result = json.loads(planned.stdout)
    assert result["status"] == "planned" and len(result["plan_fingerprint"]) == 64
    assert result["estimated_usage"]["planning_cost_usd"] == 0
    assert service.ledger.list_operations(result["task_id"]) == []
    rejected = runner.invoke(app, ["--json", "ui", "execute", result["task_id"], "--approve", "0" * 64])
    assert rejected.exit_code == 4
    assert json.loads(rejected.stdout)["error"]["code"] == "plan_changed"


def test_cli_editing_plan_stays_offline_and_execution_requires_child_schema(tmp_path, monkeypatch):
    from letsaigc.ui_analysis import cli

    service = PipelineService(tmp_path / "editing", ui_schema=4)
    monkeypatch.setattr(cli, "service", lambda: service)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    result = CliRunner().invoke(
        app,
        [
            "--json",
            "ui",
            "plan",
            "--query",
            "game UI",
            "--mode",
            "reconstruct",
            "--target",
            "scene_background",
            "--budget",
            "configs/ui-analysis/budget-example.yaml",
        ],
    )
    assert result.exit_code == 0, result.stdout
    planned = json.loads(result.stdout)
    assert planned["output_mode"] == "reconstruct"
    assert service.ledger.list_operations(planned["task_id"]) == []
    executed = CliRunner().invoke(app, [
        "--json", "ui", "execute", planned["task_id"], "--approve", planned["plan_fingerprint"],
    ])
    assert executed.exit_code == 3
    assert json.loads(executed.stdout)["error"]["code"] == "migration_required"


@pytest.mark.parametrize("schema_version", [3, 4, 5])
def test_search_plan_freezes_both_providers_without_probes(tmp_path, monkeypatch, schema_version):
    from letsaigc.schemas.pipeline import ArtifactRef
    from letsaigc.schemas.ui import UIAnalysisRequest
    from letsaigc.ui_analysis import cli

    service = PipelineService(tmp_path / "search", ui_schema=schema_version)
    monkeypatch.setattr(cli, "service", lambda: service)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    result = CliRunner().invoke(
        app,
        [
            "--json",
            "ui",
            "plan",
            "--query",
            "game HUD",
            "--max-images",
            "1",
            "--budget",
            "configs/ui-analysis/budget-example.yaml",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    plan = service.ledger.plan(payload["task_id"])
    request = UIAnalysisRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(plan.parameters["request_ref"]))
    )
    routing = json.loads(service.artifacts.read(request.input.routing_policy_ref))
    assert routing["routing"]["allowed_providers"] == ["serpapi", "tavily"]
    assert routing["routing"]["serpapi_no_cache"] is True
    assert "game HUD" not in plan.model_dump_json()
    assert service.ledger.list_operations(plan.task_id) == []
    rejected = CliRunner().invoke(
        app,
        [
            "--json",
            "ui",
            "plan",
            "--query",
            "game HUD",
            "--max-images",
            "2",
            "--budget",
            "configs/ui-analysis/budget-example.yaml",
        ],
    )
    assert rejected.exit_code == 3
