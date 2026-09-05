"""Frozen pre-UI v1 contracts: byte serialization and reservation semantics."""

import json
from pathlib import Path

from typer.testing import CliRunner

from letsaigc.cli import app
from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.ledger import Ledger
from letsaigc.schemas import TaskBudget
from letsaigc.schemas.pipeline import Cost, PipelinePlan, canonical_json, operation_id


def test_v1_plan_bytes_fingerprint_and_operation_identity():
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/contracts/ui-v1-compatibility.json").read_text())
    plan = PipelinePlan.model_validate(fixture["plan"])
    assert canonical_json(plan) == fixture["canonical"]
    assert plan.fingerprint == fixture["fingerprint"]
    assert operation_id(plan, "generate", 0) == fixture["operation_id"]
    assert TaskBudget.model_fields["max_revisions"].default == 10
    output = CliRunner().invoke(app, ["--help"])
    assert output.exit_code == 0
    for command in fixture["cli_commands"]:
        assert command in output.stdout


def test_v1_finish_still_reports_exceeding_reservation_not_approval(tmp_path):
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/contracts/ui-v1-compatibility.json").read_text())
    plan = PipelinePlan.model_validate(fixture["plan"])
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.register(plan)
    ledger.consume_approval(approve(ledger, plan.task_id, plan.fingerprint))
    operation = ledger.reserve(plan, "generate", 0, Cost(cost_usd=0.1))
    result = ledger.finish(operation.operation_id, Cost(cost_usd=0.2), {"done": True})
    assert result.state == "succeeded"
    assert result.result == {"done": True, "budget_exceeded": True}
