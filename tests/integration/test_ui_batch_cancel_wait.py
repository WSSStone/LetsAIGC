"""Bounded cancellation/reconciliation using real temporary ledger transport."""

import asyncio

import pytest
from temporalio import workflow
from test_ui_analysis_workflow import ModelBackend
from test_ui_batch import child_projection, family, run_child, start_analysis
from test_ui_batch_workflow import components, transport

from letsaigc.schemas.pipeline import Cost
from letsaigc.schemas.ui import UIObservation


async def spin_until(condition):
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0)
    assert condition(), "Workflow did not reach the expected observable state"


@pytest.mark.parametrize("acknowledge", [True, False, "native"])
def test_unknown_cancel_exposes_reconciliation_before_waiting_for_child(tmp_path, monkeypatch, acknowledge):
    service, plan, batch = family(tmp_path)
    workflow_class, input_class, registered = components(service)
    instance = workflow_class()
    events, handles, signals = [], [], []
    settled = False

    class UnknownBackend(ModelBackend):
        def inspect(self, submission):
            return UIObservation(state="succeeded", actual=Cost()) if settled else UIObservation(state="unknown")

    service.backends["ui.analyze"] = UnknownBackend("ui.analyze", service.artifacts)
    transport(monkeypatch, plan, registered, events)

    class ChildHandle(asyncio.Future):
        def cancel(self, msg=None):
            assert batch.status(plan).state == "awaiting_reconciliation"
            return True

        async def signal(self, name):
            assert name == "reconcile_cancelled"
            assert settled
            signals.append(name)
            if acknowledge == "native" and len(signals) == 1:
                raise asyncio.CancelledError
            if acknowledge:
                self.set_result(child_projection(service, self.child, "cancelled"))

    async def start_child_workflow(name, argument, **kwargs):
        handle = ChildHandle()
        handle.child = argument.plan
        prepared = start_analysis(service, argument.plan)
        operation = service.submit_step(argument.plan, prepared.binding, prepared.reservation)
        service.ledger.uncertain(operation.operation_id)
        handles.append(handle)
        instance.cancel()
        return handle

    async def wait_condition(condition, **kwargs):
        if "timeout" in kwargs and not condition():
            assert kwargs["timeout"] <= 60
            raise TimeoutError
        while not condition():
            await asyncio.sleep(0)

    monkeypatch.setattr(workflow, "start_child_workflow", start_child_workflow)
    monkeypatch.setattr(workflow, "wait_condition", wait_condition)

    async def run():
        nonlocal settled
        task = asyncio.create_task(instance.run(input_class(plan=plan, approved=True)))
        try:
            await spin_until(lambda: instance.current is not None and
                             instance.current.state.value == "awaiting_reconciliation")
            assert not task.done() and not handles[0].done()
            assert instance.active_child is handles[0]
            with service.ledger.transaction() as db:
                assert db.execute("SELECT submission_gate FROM ui_budget_groups").fetchone()[0] != "open"
                assert db.execute("SELECT COUNT(*) FROM ui_child_bindings WHERE active=1").fetchone()[0] == 1
            assert service.ledger.usage(plan.task_id, include_children=True)["unsettled"] == Cost(cost_usd=0.25)
            settled = True
            instance.reconcile()
            if not acknowledge:
                await spin_until(lambda: instance.current.stop_reason == "child_cancellation_pending")
                assert not task.done()
                assert instance.active_child is handles[0]
                handles[0].set_result(child_projection(service, handles[0].child, "cancelled"))
                instance.reconcile()
            await spin_until(task.done)
            assert handles[0].done(), "Native parent cancellation cannot discard a live child handle"
            return task.result()
        finally:
            settled = True
            for handle in handles:
                if not handle.done():
                    handle.set_result(child_projection(service, handle.child, "cancelled"))
            for _ in range(200):
                if task.done():
                    break
                instance.reconciliation = True
                await asyncio.sleep(0)
            assert task.done(), "Test transport could not complete cleanup"
            await asyncio.gather(task, return_exceptions=True)

    assert asyncio.run(run()).state.value == "cancelled"
    assert events.count("ui.batch.cancel.v1") == (2 if acknowledge is True else 3)
    assert len(handles) == 1


@pytest.mark.parametrize("boundary", ["prepare", "checkpoint", "project"])
def test_cancel_during_terminal_activity_wins_over_success(tmp_path, monkeypatch, boundary):
    service, plan, batch = family(tmp_path)
    workflow_class, input_class, registered = components(service)
    instance = workflow_class()
    events = []
    transport(monkeypatch, plan, registered, events)
    child = batch.prepare(plan).child
    batch.complete(plan, run_child(service, child))
    if boundary == "prepare":
        child = batch.prepare(plan).child
        batch.complete(plan, run_child(service, child))
    execute = workflow.execute_activity

    async def execute_activity(name, argument, **kwargs):
        result = await execute(name, argument, **kwargs)
        if name == f"ui.batch.{boundary}.v1" and (
            boundary != "project" or argument.run.state.value == "succeeded"
        ):
            instance.cancel()
        return result

    async def start_child_workflow(name, argument, **kwargs):
        async def child():
            return run_child(service, argument.plan)
        return asyncio.create_task(child())

    async def wait_condition(condition, **kwargs):
        while not condition():
            await asyncio.sleep(0)

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "start_child_workflow", start_child_workflow)
    monkeypatch.setattr(workflow, "wait_condition", wait_condition)

    result = asyncio.run(instance.run(input_class(plan=plan, approved=True)))
    assert result.state.value == "cancelled"
    assert "ui.batch.cancel.v1" in events
    assert batch.status(plan).state == "cancelled"


@pytest.mark.parametrize("editing", [False, True])
def test_cancel_reconciliation_signal_only_wakes_cancelled_children(tmp_path, monkeypatch, editing):
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from letsaigc.execution.temporal.ui_editing_workflow import UIEditingWorkflow
    from letsaigc.execution.temporal.ui_workflow import UIAnalysisWorkflow

    service, plan, _ = family(tmp_path)
    cls = UIEditingWorkflow if editing else UIAnalysisWorkflow
    child = cls()
    child.current = child_projection(service, plan, "awaiting_reconciliation")
    child.cancelling = False
    published = []

    async def publish(state, code):
        published.append(state)

    async def wait_condition(condition, **kwargs):
        while not condition():
            await asyncio.sleep(0)

    monkeypatch.setattr(child, "publish", publish)
    monkeypatch.setattr(workflow, "wait_condition", wait_condition)

    async def run():
        SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(cls))
        task = asyncio.create_task(child.await_reconciliation("outcome_unknown"))
        try:
            await spin_until(lambda: bool(published))
            child.reconcile_cancelled()
            await asyncio.sleep(0)
            assert not task.done()
            assert child.cancelled is False
            assert child.pending is None
            child.cancelled = child.cancelling = True
            child.reconcile_cancelled()
            await spin_until(task.done)
            task.result()
            assert child.pending is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("reason", ["outcome_unknown", "resource_release_unknown", "approval_required"])
def test_complete_gate_waits_without_starting_another_child(tmp_path, monkeypatch, reason):
    from temporalio.exceptions import ActivityError, ApplicationError

    service, plan, _ = family(tmp_path)
    workflow_class, input_class, registered = components(service)
    instance = workflow_class()
    events, started = [], []
    transport(monkeypatch, plan, registered, events)
    execute = workflow.execute_activity

    async def execute_activity(name, argument, **kwargs):
        try:
            return await execute(name, argument, **kwargs)
        except ApplicationError as cause:
            error = ActivityError("gate", scheduled_event_id=1, started_event_id=2,
                                  identity="offline", activity_type=name, activity_id="gate", retry_state=None)
            raise error from cause

    async def start_child_workflow(name, argument, **kwargs):
        started.append(argument.plan.task_id)
        future = asyncio.Future()
        future.set_result(child_projection(service, argument.plan, "failed", reason=reason))
        return future

    async def wait_condition(condition, **kwargs):
        while not condition():
            await asyncio.sleep(0)

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "start_child_workflow", start_child_workflow)
    monkeypatch.setattr(workflow, "wait_condition", wait_condition)

    async def run():
        task = asyncio.create_task(instance.run(input_class(plan=plan, approved=True)))
        try:
            await spin_until(lambda: task.done() or instance.current is not None and
                             instance.current.state.value == "awaiting_reconciliation")
            assert not task.done()
            assert instance.current.stop_reason == reason
            assert instance.active_child is not None
            instance.reconcile()
            await asyncio.sleep(0)
            assert len(started) == 1
        finally:
            instance.cancel()
            await spin_until(task.done)
            await task

    asyncio.run(run())


@pytest.mark.parametrize("editing", [False, True])
def test_parent_signal_rechecks_real_child_cancellation_without_new_submissions(tmp_path, monkeypatch, editing):
    from temporalio import activity

    from letsaigc.execution.temporal.ui_activities import UIActivities
    from letsaigc.execution.temporal.ui_editing_activities import UIEditingActivities
    from letsaigc.execution.temporal.ui_editing_workflow import UIEditingWorkflow
    from letsaigc.execution.temporal.ui_messages import UIEditingWorkflowInput, UIWorkflowInput
    from letsaigc.execution.temporal.ui_workflow import UIAnalysisWorkflow

    service, plan, batch = family(tmp_path)
    child = batch.prepare(plan).child
    settled = False

    class UnknownBackend(ModelBackend):
        def inspect(self, submission):
            return UIObservation(state="succeeded", actual=Cost()) if settled else UIObservation(state="unknown")

    backend = UnknownBackend("ui.analyze", service.artifacts)
    service.backends["ui.analyze"] = backend
    prepared = start_analysis(service, child)
    operation = service.submit_step(child, prepared.binding, prepared.reservation)
    service.ledger.uncertain(operation.operation_id)
    adapters = [*UIActivities(service).registered(), *UIEditingActivities(service).registered()]
    registered = {activity._Definition.must_from_callable(fn).name: fn for fn in adapters}
    events = []
    transport(monkeypatch, child, registered, events)
    instance = UIEditingWorkflow() if editing else UIAnalysisWorkflow()
    input_class = UIEditingWorkflowInput if editing else UIWorkflowInput

    async def wait_condition(condition, **kwargs):
        while not condition():
            await asyncio.sleep(0)

    monkeypatch.setattr(workflow, "wait_condition", wait_condition)

    async def run():
        nonlocal settled
        task = asyncio.create_task(instance.run(input_class(plan=child, approved=True, cancelled=True)))
        try:
            await spin_until(lambda: instance.current is not None and
                             instance.current.state.value == "awaiting_reconciliation")
            assert service.ledger.get(operation.operation_id).state == "outcome_unknown"
            settled = True
            instance.reconcile_cancelled()
            await spin_until(task.done)
            assert task.result().state.value == "cancelled"
        finally:
            settled = True
            instance.reconciliation = True
            await spin_until(task.done)
            await task

    asyncio.run(run())
    assert backend.calls == 1
    assert events.count("ui.edit.cancel.v1" if editing else "ui.cancel.v1") == 2
    assert not any(name.endswith("submit.v1") for name in events)
    assert service.ledger.usage(plan.task_id, include_children=True)["unsettled"] == Cost()


def test_search_prepare_unknown_waits_for_reconcile_without_resubmitting_supply(tmp_path, monkeypatch):
    from temporalio.exceptions import ActivityError, ApplicationError

    service, plan, _ = family(tmp_path, source="search")
    workflow_class, input_class, registered = components(service)
    instance = workflow_class()
    events, started = [], []
    backend = service.backends["ui.search"]
    inspect = backend.inspect
    settled = False
    monkeypatch.setattr(
        backend, "inspect", lambda receipt: inspect(receipt) if settled else UIObservation(state="unknown"),
    )
    transport(monkeypatch, plan, registered, events)
    info = workflow.info()
    info.is_continue_as_new_suggested = lambda: False
    monkeypatch.setattr(workflow, "info", lambda: info)
    execute = workflow.execute_activity

    async def execute_activity(name, argument, **kwargs):
        try:
            return await execute(name, argument, **kwargs)
        except ApplicationError as cause:
            error = ActivityError("prepare", scheduled_event_id=1, started_event_id=2,
                                  identity="offline", activity_type=name, activity_id="prepare", retry_state=None)
            raise error from cause

    async def start_child_workflow(name, argument, **kwargs):
        started.append(argument.plan.task_id)

        async def child():
            return run_child(service, argument.plan)

        return asyncio.create_task(child())

    async def wait_condition(condition, **kwargs):
        while not condition():
            await asyncio.sleep(0)

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "start_child_workflow", start_child_workflow)
    monkeypatch.setattr(workflow, "wait_condition", wait_condition)

    async def run():
        nonlocal settled
        task = asyncio.create_task(instance.run(input_class(plan=plan, approved=True)))
        try:
            await spin_until(lambda: task.done() or instance.current is not None and
                             instance.current.state.value == "awaiting_reconciliation")
            assert not task.done(), "Uncertain search supply must keep its workflow available for reconcile"
            operation = service.ledger.list_operations(plan.task_id)[0]
            assert operation.state == "outcome_unknown"
            assert backend.calls == 1 and not started
            instance.reconcile()
            await asyncio.sleep(0)
            unchanged = service.ledger.get(operation.operation_id)
            assert unchanged.provider_request_id == operation.provider_request_id
            assert unchanged.reserved == operation.reserved
            assert backend.calls == 1 and not started
            settled = True
            service.observe_step(plan, operation.operation_id)
            service.collect_step(plan, operation.operation_id)
            instance.reconcile()
            await spin_until(task.done)
            assert task.result().state.value == "succeeded"
        finally:
            if not task.done():
                settled = True
                instance.cancel()
                await spin_until(task.done)
            await task

    asyncio.run(run())
    assert backend.calls == 1
    assert len(started) == 2
