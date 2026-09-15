"""SDK cancellation wrappers must reach durable batch cleanup.

Uses isolated ledgers and local transport; this is not a live Temporal test.
"""

import asyncio
from types import SimpleNamespace

import pytest
from temporalio import activity, workflow
from temporalio.exceptions import ActivityError, CancelledError
from test_ui_batch import family, run_child
from test_ui_batch_workflow import components


@pytest.mark.parametrize("cancelled_activity", ["prepare", "complete"])
def test_native_activity_cancellation_closes_batch_gate(tmp_path, monkeypatch, cancelled_activity):
    service, plan, batch = family(tmp_path)
    workflow_class, input_class, registered = components(service)
    events, started = [], []

    async def execute_activity(name, argument, **kwargs):
        events.append(name)
        result = registered[name](argument)
        if name == f"ui.batch.{cancelled_activity}.v1":
            # The worker committed the activity's durable write, but the SDK
            # delivered a native cancellation instead of its result.
            error = ActivityError(
                "cancelled", scheduled_event_id=1, started_event_id=2,
                identity="offline", activity_type=name, activity_id="cancelled",
                retry_state=None,
            )
            error.__cause__ = CancelledError("cancelled")
            raise error
        return result

    async def start_child_workflow(name, argument, **kwargs):
        started.append(argument.plan.task_id)

        async def execute_child():
            return run_child(service, argument.plan)

        return asyncio.create_task(execute_child())

    async def wait_condition(condition, **kwargs):
        if not condition():
            await asyncio.Future()

    monkeypatch.setattr(activity, "heartbeat", lambda *args: None)
    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "start_child_workflow", start_child_workflow)
    monkeypatch.setattr(workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(
        workflow_id=plan.task_id, run_id="native-cancel", is_continue_as_new_suggested=lambda: False,
    ))

    async def run():
        return await asyncio.wait_for(workflow_class().run(input_class(plan=plan, approved=True)), timeout=5)

    result = asyncio.run(run())
    assert result.state.value == "cancelled"
    assert "ui.batch.cancel.v1" in events
    assert len(started) == (1 if cancelled_activity == "complete" else 0)
    assert batch.status(plan).state == "cancelled"
    with service.ledger.transaction() as db:
        assert db.execute("SELECT submission_gate FROM ui_budget_groups").fetchone()[0] != "open"
        assert db.execute("SELECT COUNT(*) FROM ui_child_bindings WHERE active=1").fetchone()[0] == 0
