from typer.testing import CliRunner

import letsaigc.cli as cli_module
from letsaigc.cli import app


def test_cli_exposes_agent_and_preserves_expert_groups() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("agent", "models", "comfy", "workflow", "video", "sprites", "drama", "train", "runpack"):
        assert command in result.stdout


def test_json_run_never_prompts_or_executes(monkeypatch) -> None:
    class FakeOrchestrator:
        def plan(self, *args, **kwargs):
            return {
                "id": "task-json",
                "session_id": "session-json",
                "state": "awaiting_approval",
                "plan_fingerprint": "0" * 64,
            }

        def execute(self, *args, **kwargs):
            raise AssertionError("JSON run must not execute")

    monkeypatch.setattr(cli_module, "AgentOrchestrator", FakeOrchestrator)
    result = CliRunner().invoke(
        app,
        [
            "--json",
            "agent",
            "run",
            "potion icon",
            "--budget",
            "configs/agent/budget-local.yaml",
        ],
    )
    assert result.exit_code == 0
    assert "awaiting_approval" in result.stdout
    assert "Execute this exact plan" not in result.stdout
