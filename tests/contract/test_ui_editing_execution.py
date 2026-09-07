"""The trusted CLI exposes exact children without granting root GPU authority."""

import json

import pytest
from test_ui_editing_entrypoints import invoke
from test_ui_editing_entrypoints import reviewed as reviewed_fixture
from typer.testing import CliRunner

from letsaigc.cli import app
from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.migrations import migrate_ui_ledger
from letsaigc.schemas.pipeline import ArtifactRef
from letsaigc.ui_analysis import cli, runtime
from letsaigc.ui_analysis.editing import EditingExecution


@pytest.fixture
def reviewed(tmp_path, monkeypatch):
    return reviewed_fixture.__wrapped__(tmp_path, monkeypatch)


def test_review_plan_exposes_exact_child_and_root_execute_cannot_approve_it(reviewed, monkeypatch):
    service, _, budget = reviewed
    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    planned = invoke("plan", "--reviewed-task", "reviewed-source", "--mode", "decompose", "--budget", str(budget))
    pending = planned["editing"]["pending_approvals"]
    assert len(pending) == 1
    child = service.ledger.plan(pending[0]["task_id"])
    assert child.workflow_type == "ui_segmentation"
    assert pending[0]["plan_fingerprint"] == child.fingerprint
    assert len(child.fingerprint) == 64
    assert EditingExecution(service).validate_current(child)
    root_only = invoke("execute", planned["task_id"], "--approve", planned["plan_fingerprint"])
    assert root_only["status"] == "awaiting_approval"
    assert root_only["editing"]["pending_approvals"][0]["task_id"] == child.task_id
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
    received = []

    async def start(instance, root, receipt):
        received.append((root.task_id, receipt.task_id, receipt.plan_fingerprint))

    monkeypatch.setattr(cli, "start_approved", start)
    monkeypatch.setattr(cli, "approve", approve)
    monkeypatch.setattr(runtime, "preflight_child", lambda *args: None)
    executed = invoke("execute", child.task_id, "--approve", child.fingerprint)
    assert executed["root_task_id"] == planned["task_id"]
    assert received == [(planned["task_id"], child.task_id, child.fingerprint)]
    # Trusted CLI records a receipt, but only the worker can consume it.
    with service.ledger.transaction() as db:
        assert tuple(db.execute("SELECT COUNT(*),SUM(consumed) FROM approvals").fetchone()) == (1, 0)
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


def test_child_layout_rebinding_preserves_review_geometry_and_sam_permit(reviewed):
    service, _, budget = reviewed
    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    planned = invoke("plan", "--reviewed-task", "reviewed-source", "--mode", "decompose", "--budget", str(budget))
    child = service.ledger.plan(planned["editing"]["pending_approvals"][0]["task_id"])
    from letsaigc.schemas.ui import UISegmentationRequest
    from letsaigc.vision.base import issue_sam_permit
    from letsaigc.vision.client import SAMOperationBackend

    request = UISegmentationRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(child.parameters["request_ref"]))
    )
    selection = json.loads(service.artifacts.read(request.selection_ref))
    local_layout = ArtifactRef.model_validate(selection["sources"][0]["layout_ref"])
    assert local_layout.task_id == child.task_id
    layout = json.loads(service.artifacts.read(local_layout))
    assert layout["task_id"] == child.task_id
    assert layout["elements"][0]["bbox"] == [1, 1, 8, 8]
    execution = EditingExecution(service)
    receipt = approve(service.ledger, child.task_id, child.fingerprint)
    service.ledger.consume_approval(receipt)
    binding = execution.binding(child)
    from letsaigc.schemas.pipeline import Cost

    operation = service.ledger.reserve(
        child, binding.step_id, 0, Cost(gpu_minutes=1), resource="local-gpu", ui_binding=binding
    )
    service.ledger.begin_submit(operation.operation_id)
    adapter = SAMOperationBackend(service.ledger, service.artifacts, None, signing_key="x" * 32)
    job = adapter._job(operation.operation_id)
    assert issue_sam_permit(service.ledger, service.artifacts, job, "x" * 32)
    from letsaigc.schemas.ui import UISelection
    from letsaigc.vision.segmentation import _layout_boxes

    source = UISelection.model_validate(selection).sources[0]
    boxes, _ = _layout_boxes(service.artifacts, source, request.canonical_ref, 20, 20)
    assert boxes["icon"] == (1, 1, 8, 8)


def test_child_exact_approval_mismatch_does_not_touch_ledger(reviewed):
    service, _, budget = reviewed
    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    planned = invoke("plan", "--reviewed-task", "reviewed-source", "--mode", "decompose", "--budget", str(budget))
    child_id = planned["editing"]["pending_approvals"][0]["task_id"]
    failed = CliRunner().invoke(app, ["--json", "ui", "execute", child_id, "--approve", planned["plan_fingerprint"]])
    assert failed.exit_code == 4
    assert json.loads(failed.stdout)["error"]["code"] == "plan_changed"
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


def test_completed_child_execute_returns_existing_result_without_new_approval(reviewed, monkeypatch):
    from letsaigc.schemas.pipeline import Cost

    service, _, budget = reviewed
    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    planned = invoke("plan", "--reviewed-task", "reviewed-source", "--mode", "decompose", "--budget", str(budget))
    child = service.ledger.plan(planned["editing"]["pending_approvals"][0]["task_id"])
    receipt = approve(service.ledger, child.task_id, child.fingerprint)
    service.ledger.consume_approval(receipt)
    binding = EditingExecution(service).binding(child)
    operation = service.ledger.reserve(child, "segment", 0, Cost(gpu_minutes=1),
                                       resource="local-gpu", ui_binding=binding)
    service.ledger.finish_ui(operation.operation_id, Cost(), {"artifacts": []})
    monkeypatch.setattr(runtime, "preflight_child", lambda *args: pytest.fail("terminal child preflight"))
    result = invoke("execute", child.task_id, "--approve", child.fingerprint)
    assert result["status"] == "already_completed"
    with service.ledger.transaction() as db:
        assert tuple(db.execute("SELECT COUNT(*),SUM(consumed) FROM approvals").fetchone()) == (1, 1)


def test_cli_selection_replacement_invalidates_old_child_before_new_approval(reviewed, tmp_path):
    service, _, budget = reviewed
    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    planned = invoke("plan", "--reviewed-task", "reviewed-source", "--mode", "decompose", "--budget", str(budget))
    previous = service.ledger.plan(planned["editing"]["pending_approvals"][0]["task_id"])
    from letsaigc.pipelines.errors import PipelineError
    from letsaigc.ui_analysis.selection import selection_catalog

    prior_receipt = approve(service.ledger, previous.task_id, previous.fingerprint)
    catalog = selection_catalog(service.artifacts, planned["task_id"])
    selection_ref = catalog.selection_ref or catalog.candidates[0].selection_ref
    payload = json.loads(service.artifacts.read(selection_ref))
    payload["sources"][0]["target_regions"] = [{"kind": "bbox", "xyxy": [2, 2, 9, 9]}]
    override = tmp_path / "selection.json"
    override.write_text(json.dumps(payload))
    selected = invoke("select", planned["task_id"], "--selection", str(override))
    pending = selected["editing"]["pending_approvals"]
    assert len(pending) == 1 and pending[0]["task_id"] != previous.task_id
    with pytest.raises(PipelineError, match="selection"):
        service.ledger.consume_approval(prior_receipt)
    with service.ledger.transaction() as db:
        assert db.execute("SELECT SUM(consumed) FROM approvals").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
