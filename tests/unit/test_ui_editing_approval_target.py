"""Approval routing must not depend on the planner's older candidate."""
from types import SimpleNamespace

import pytest

from letsaigc.execution.temporal.ui_editing_workflow import UIEditingWorkflow
from letsaigc.schemas.pipeline import ApprovalRequest, PipelineState


def test_new_candidate_receipt_is_queued_for_activity_validation(monkeypatch):
    from temporalio import workflow

    monkeypatch.setattr(workflow, "patched", lambda _: True)
    instance = UIEditingWorkflow()
    instance.phase_index = 7
    instance.child = SimpleNamespace(task_id="old", fingerprint="a" * 64)
    instance.current = SimpleNamespace(state=PipelineState.awaiting_approval)
    instance.accepted_request = None
    instance.child_pending = None
    request = ApprovalRequest(task_id="new", plan_fingerprint="b" * 64,
                              request_id="approval-test", decision="approve")
    assert instance.approval(request) == "registered_pending_validation"
    assert instance.child.task_id == "old"
    assert instance.child_pending == request
    assert instance.approval(request) == "already_registered"


def test_legacy_workflow_still_rejects_mismatched_candidate(monkeypatch):
    from temporalio import workflow
    from temporalio.exceptions import ApplicationError

    monkeypatch.setattr(workflow, "patched", lambda _: False)
    instance = UIEditingWorkflow()
    instance.phase_index = 7
    instance.child = SimpleNamespace(task_id="old", fingerprint="a" * 64)
    instance.current = SimpleNamespace(state=PipelineState.awaiting_approval)
    request = ApprovalRequest(task_id="new", plan_fingerprint="b" * 64,
                              request_id="approval-test", decision="approve")
    with pytest.raises(ApplicationError, match="approval_mismatch"):
        instance.approval(request)


@pytest.mark.parametrize("enabled", [False, True])
def test_unknown_image_wait_accepts_new_target_only_on_versioned_path(monkeypatch, enabled):
    from temporalio import workflow
    from temporalio.exceptions import ApplicationError

    monkeypatch.setattr(workflow, "patched", lambda name: enabled if name == "ui-cloud-image-retry-v1" else True)
    instance = UIEditingWorkflow()
    instance.phase_index = 7
    instance.child = SimpleNamespace(task_id="old", fingerprint="a" * 64)
    instance.child_approved = True
    instance.child_pending = None
    instance.accepted_request = None
    instance.reconciliation = False
    instance.current = SimpleNamespace(state=PipelineState.awaiting_reconciliation)
    request = ApprovalRequest(task_id="new", plan_fingerprint="b" * 64,
                              request_id="retry-approval", decision="approve")
    if not enabled:
        with pytest.raises(ApplicationError, match="approval_closed"):
            instance.approval(request)
        assert instance.child_approved and not instance.reconciliation
    else:
        assert instance.approval(request) == "registered_pending_validation"
        assert instance.child.task_id == "old"
        assert instance.child_pending == request
        assert instance.reconciliation and not instance.child_approved
        assert instance.approval(request) == "already_registered"
