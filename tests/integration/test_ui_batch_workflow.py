"""Batch Temporal orchestration contracts with SDK sandbox and local transport.

Real temporary ledger/artifact execution is used for each child. This is not a
Temporal server replay or a live model acceptance test.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from temporalio import activity, workflow
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from test_ui_batch import child_projection, family, run_child

from letsaigc.execution.temporal.activities import PipelineActivities
from letsaigc.schemas.pipeline import validate_payload


def components(service):
    from letsaigc.execution.temporal.ui_batch_activities import UIBatchActivities
    from letsaigc.execution.temporal.ui_batch_messages import UIBatchWorkflowInput
    from letsaigc.execution.temporal.ui_batch_workflow import UIBatchWorkflow

    adapters = [*PipelineActivities(service).registered(), *UIBatchActivities(service).registered()]
    registered = {activity._Definition.must_from_callable(function).name: function for function in adapters}
    return UIBatchWorkflow, UIBatchWorkflowInput, registered


def transport(monkeypatch, plan, registered, events):
    async def execute_activity(name, argument, **kwargs):
        validate_payload(argument)
        events.append(name)
        return registered[name](argument)

    monkeypatch.setattr(activity, "heartbeat", lambda *args: None)
    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "now", lambda: datetime.now(UTC))
    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(
        workflow_id=plan.task_id, run_id="offline-batch", is_continue_as_new_suggested=lambda: True,
    ))


@pytest.mark.parametrize("source", ["manual", "search"])
def test_batch_continue_as_new_requires_settled_child_and_finished_handlers(tmp_path, monkeypatch, source):
    service, plan, _ = family(tmp_path, source=source, request_overrides={"resources": {"active_seconds": 120}})
    workflow_class, input_class, registered = components(service)
    events, started, continuations = [], [], []
    transport(monkeypatch, plan, registered, events)
    handlers = {"finished": True}

    class Continued(BaseException):
        def __init__(self, argument):
            self.argument = argument

    def continue_as_new(argument):
        validate_payload(argument)
        assert handlers["finished"]
        with service.ledger.transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM ui_child_bindings WHERE active=1").fetchone()[0] == 0
        assert "ui.batch.checkpoint.v1" in events
        assert argument.plan.fingerprint == plan.fingerprint
        assert argument.approved is True
        continuations.append(argument)
        raise Continued(argument)

    async def start_child_workflow(name, argument, **kwargs):
        assert name == "letsaigc.ui.analysis.v1"
        assert kwargs["id"] == argument.plan.task_id
        assert kwargs["parent_close_policy"] == workflow.ParentClosePolicy.REQUEST_CANCEL
        assert argument.approved is True
        assert argument.active_limit_seconds == 120
        assert argument.plan.task_id not in started
        with service.ledger.transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM ui_child_bindings WHERE active=1").fetchone()[0] == 1
        started.append(argument.plan.task_id)
        handlers["finished"] = False

        async def execute_child():
            await asyncio.sleep(0)
            return run_child(service, argument.plan)

        return asyncio.create_task(execute_child())

    def all_handlers_finished():
        return handlers["finished"]

    async def wait_condition(condition, **kwargs):
        if condition is all_handlers_finished:
            handlers["finished"] = True
        for _ in range(10):
            if condition():
                break
            await asyncio.sleep(0)
        assert condition(), "Unexpected unresolved workflow condition"

    monkeypatch.setattr(workflow, "start_child_workflow", start_child_workflow)
    monkeypatch.setattr(workflow, "all_handlers_finished", all_handlers_finished)
    monkeypatch.setattr(workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(workflow, "continue_as_new", continue_as_new)

    async def run():
        SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(workflow_class))
        argument = input_class(plan=plan, approved=True, observations_per_run=1)
        while True:
            try:
                return await workflow_class().run(argument)
            except Continued as continuation:
                assert len(continuations) <= 3
                argument = continuation.argument

    result = asyncio.run(run())
    assert result.state.value == "succeeded"
    assert len(started) == 2
    assert continuations
    assert service.backends["ui.analyze"].calls == service.backends["ui.ocr"].calls == 2
    assert events.count("ui.batch.complete.v1") == 2


def test_parent_cancel_closes_ledger_gate_before_requesting_child_cancellation(tmp_path, monkeypatch):
    service, plan, _ = family(tmp_path)
    workflow_class, input_class, registered = components(service)
    events, cancelled_children = [], []
    transport(monkeypatch, plan, registered, events)
    instance = workflow_class()

    class ChildHandle(asyncio.Future):
        def __init__(self, child):
            super().__init__()
            self.child = child

        def cancel(self, msg=None):
            with service.ledger.transaction() as db:
                gate = db.execute(
                    "SELECT submission_gate FROM ui_budget_groups WHERE root_task_id=?", (plan.task_id,),
                ).fetchone()[0]
            assert gate != "open", "Root gate must close before REQUEST_CANCEL reaches the child"
            cancelled_children.append(self.child.task_id)
            self.set_result(child_projection(service, self.child, "cancelled", reason="cancellation_confirmed"))
            return True

    async def start_child_workflow(name, argument, **kwargs):
        assert kwargs["parent_close_policy"] == workflow.ParentClosePolicy.REQUEST_CANCEL
        handle = ChildHandle(argument.plan)
        instance.cancel()
        return handle

    async def wait_condition(condition, **kwargs):
        assert condition(), "Parent cancellation must wake the coordinator"

    monkeypatch.setattr(workflow, "start_child_workflow", start_child_workflow)
    monkeypatch.setattr(workflow, "all_handlers_finished", lambda: True)
    monkeypatch.setattr(workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(workflow, "continue_as_new", lambda argument: pytest.fail("Cancellation must not continue"))

    async def run():
        SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(workflow_class))
        return await asyncio.wait_for(instance.run(input_class(plan=plan, approved=True)), timeout=5)

    result = asyncio.run(run())
    assert result.state.value == "cancelled"
    assert len(cancelled_children) == 1
    assert "ui.batch.cancel.v1" in events
    assert service.backends["ui.analyze"].calls == 0
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM ui_child_bindings").fetchone()[0] == 1
