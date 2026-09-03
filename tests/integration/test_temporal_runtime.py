"""Opt-in integration against the pinned local CLI; never downloads or calls a GPU."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytest.importorskip("temporalio")
from temporalio import workflow
from temporalio.client import WorkflowUpdateFailedError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from letsaigc.execution.temporal.activities import PipelineActivities
from letsaigc.execution.temporal.messages import WorkflowInput
from letsaigc.execution.temporal.smoke_workflow import ComfyGenerationWorkflow, SmokeWorkflow
from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.contracts import Observation
from letsaigc.pipelines.service import PipelineService
from letsaigc.pipelines.simulation import SimulationBackend
from letsaigc.schemas.pipeline import ApprovalRequest, PipelineRun, PipelineState

CLI = os.getenv("LETSAIGC_TEMPORAL_TEST_CLI")
pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(not CLI, reason="Set LETSAIGC_TEMPORAL_TEST_CLI to verified local CLI"),
]


async def wait_state(handle, expected, *, reason=None):
    async def wait():
        while True:
            state = await handle.query("state", result_type=PipelineRun | None)
            if state and state.state == expected and (reason is None or state.stop_reason == reason):
                return state
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(wait(), 30)


async def environment(path: Path, port=None):
    return await WorkflowEnvironment.start_local(
        dev_server_existing_path=CLI,
        dev_server_database_filename=str(path),
        data_converter=pydantic_data_converter,
        port=port,
        ui=False,
        dev_server_log_level="error",
    )


def worker(client, service, executor):
    activities = PipelineActivities(service)
    return Worker(
        client,
        task_queue="test-pipelines",
        workflows=[SmokeWorkflow, ComfyGenerationWorkflow],
        activities=activities.registered(),
        activity_executor=executor,
    )


async def begin(client, plan, **kwargs):
    return await client.start_workflow(
        SmokeWorkflow.run,
        WorkflowInput(plan=plan, poll_seconds=0.05, activity_timeout_seconds=5, **kwargs),
        id=plan.task_id,
        task_queue="test-pipelines",
        result_type=PipelineRun,
    )


async def approved(handle, service, plan):
    receipt = approve(service.ledger, plan.task_id, plan.fingerprint)
    return await handle.execute_update("approval", receipt, id=receipt.request_id, result_type=str)


def test_temporal_approval_revisions_replay_and_projection(tmp_path):
    async def scenario():
        service = PipelineService(tmp_path / "domain")
        plan = service.smoke_plan("revisions", revisions=2, accept_after=2)
        async with await environment(tmp_path / "temporal.sqlite") as env:
            with ThreadPoolExecutor(4) as executor:
                async with worker(env.client, service, executor):
                    handle = await begin(env.client, plan)
                    await wait_state(handle, PipelineState.awaiting_approval)
                    bad = ApprovalRequest(
                        request_id="bad", task_id=plan.task_id, plan_fingerprint="0" * 64, decision="approve"
                    )
                    with pytest.raises(WorkflowUpdateFailedError):
                        await handle.execute_update("approval", bad)
                    forged = bad.model_copy(update={"plan_fingerprint": plan.fingerprint, "request_id": "forged"})
                    await handle.execute_update("approval", forged)
                    await wait_state(handle, PipelineState.awaiting_approval, reason="untrusted_approval")
                    await approved(handle, service, plan)
                    await approved(handle, service, plan)
                    result = await asyncio.wait_for(handle.result(), 30)
                    assert result.state == PipelineState.succeeded
                    assert len(result.steps) == 3
                    assert len(result.artifacts) == 3
                    with sqlite3.connect(service.root / "simulation.sqlite") as db:
                        assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 3
                    history = await handle.fetch_history()
                    (tmp_path / "revisions-history.json").write_text(history.to_json(), encoding="utf-8")
                    await Replayer(workflows=[SmokeWorkflow], data_converter=pydantic_data_converter).replay_workflow(
                        history
                    )
                    from letsaigc.execution.temporal.projection import project

                    path = service.root / "tasks" / plan.task_id / "manifest.json"
                    first = path.read_bytes()
                    project(service, result)
                    assert path.read_bytes() == first
                    path.unlink()
                    project(service, result)
                    assert path.exists()
        return result

    assert asyncio.run(scenario()).revision == 2


class SlowSimulation(SimulationBackend):
    def __init__(self, path):
        super().__init__(path)
        self.polls = 0

    def inspect(self, receipt):
        self.polls += 1
        if self.polls < 4:
            return Observation(state="running")
        return super().inspect(receipt)


def test_temporal_continue_as_new_keeps_operation_and_budget(tmp_path):
    async def scenario():
        backend = SlowSimulation(tmp_path / "provider.sqlite")
        service = PipelineService(tmp_path / "domain", backends={"simulation.generate": backend})
        plan = service.smoke_plan("continue-new")
        async with await environment(tmp_path / "temporal.sqlite") as env:
            with ThreadPoolExecutor(4) as executor:
                async with worker(env.client, service, executor):
                    handle = await begin(env.client, plan, observations_per_run=1)
                    await wait_state(handle, PipelineState.awaiting_approval)
                    await approved(handle, service, plan)
                    result = await asyncio.wait_for(handle.result(), 30)
                    assert result.temporal_run_id != handle.first_execution_run_id
                    assert len(service.ledger.list_operations(plan.task_id)) == 1
                    with sqlite3.connect(tmp_path / "provider.sqlite") as db:
                        assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
                    history = await handle.fetch_history()
                    await Replayer(workflows=[SmokeWorkflow], data_converter=pydantic_data_converter).replay_workflow(
                        history
                    )
                    assert result.state == PipelineState.succeeded

    asyncio.run(scenario())


def test_temporal_cancel_still_cleans_up_when_projection_is_unavailable(tmp_path, monkeypatch):
    import letsaigc.execution.temporal.activities as module

    def unavailable(service, run):
        raise OSError("projection unavailable")

    monkeypatch.setattr(module, "project", unavailable)

    async def scenario():
        service = PipelineService(tmp_path / "domain")
        plan = service.smoke_plan("cancel-no-projection")
        async with await environment(tmp_path / "temporal.sqlite") as env:
            with ThreadPoolExecutor(4) as executor:
                async with worker(env.client, service, executor):
                    handle = await begin(env.client, plan)
                    await wait_state(handle, PipelineState.awaiting_approval)
                    await handle.execute_update("cancel")
                    result = await asyncio.wait_for(handle.result(), 30)
                    assert result.state == PipelineState.cancelled
                    assert service.ledger.list_operations(plan.task_id) == []

    asyncio.run(scenario())


def test_temporal_completed_activity_survives_worker_kill(tmp_path):
    import subprocess
    import sys

    async def scenario():
        service = PipelineService(tmp_path / "domain")
        plan = service.smoke_plan("completed-before-kill")
        helper = Path(__file__).parents[1] / "fixtures" / "temporal" / "worker_process.py"
        async with await environment(tmp_path / "temporal.sqlite") as env:
            address = env.client.service_client.config.target_host
            with (tmp_path / "worker.log").open("wb") as log:

                def launch(mode):
                    return subprocess.Popen(
                        [sys.executable, str(helper), address, str(service.root), mode],
                        stdout=log,
                        stderr=log,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )

                process = launch("running")
                try:
                    handle = await begin(env.client, plan)
                    await wait_state(handle, PipelineState.awaiting_approval)
                    await approved(handle, service, plan)

                    async def completed_observation():
                        while True:
                            history = await handle.fetch_history()
                            scheduled = {
                                event.event_id
                                for event in history.events
                                if event.HasField("activity_task_scheduled_event_attributes")
                                and event.activity_task_scheduled_event_attributes.activity_type.name
                                == "pipeline.observe.v1"
                            }
                            if any(
                                event.HasField("activity_task_completed_event_attributes")
                                and event.activity_task_completed_event_attributes.scheduled_event_id in scheduled
                                for event in history.events
                            ):
                                return
                            await asyncio.sleep(0.05)

                    await asyncio.wait_for(completed_observation(), 30)
                    assert service.ledger.list_operations(plan.task_id)[0].provider_request_id
                finally:
                    process.kill()
                    process.wait(timeout=10)
                process = launch("recover")
                try:
                    result = await asyncio.wait_for(handle.result(), 30)
                    assert result.state == PipelineState.succeeded
                    with sqlite3.connect(service.root / "provider.sqlite") as db:
                        assert db.execute("SELECT COUNT(*) FROM submission_calls").fetchone()[0] == 1
                        assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
                finally:
                    process.kill()
                    process.wait(timeout=10)

    asyncio.run(scenario())


def test_temporal_worker_process_dies_after_provider_acceptance(tmp_path):
    import subprocess
    import sys

    async def scenario():
        service = PipelineService(tmp_path / "domain")
        plan = service.smoke_plan("process-crash")
        helper = Path(__file__).parents[1] / "fixtures" / "temporal" / "worker_process.py"
        async with await environment(tmp_path / "temporal.sqlite") as env:
            address = env.client.service_client.config.target_host
            with (tmp_path / "worker.log").open("wb") as log:
                process = subprocess.Popen(
                    [sys.executable, str(helper), address, str(service.root), "crash"],
                    stdout=log,
                    stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    handle = await begin(env.client, plan)
                    await wait_state(handle, PipelineState.awaiting_approval)
                    await approved(handle, service, plan)

                    async def stopped():
                        while process.poll() is None:
                            await asyncio.sleep(0.05)

                    await asyncio.wait_for(stopped(), 20)
                    assert process.returncode == 23
                    operation = service.ledger.list_operations(plan.task_id)[0]
                    assert operation.state == "submitting"
                    with sqlite3.connect(service.root / "provider.sqlite") as db:
                        assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)
                process = subprocess.Popen(
                    [sys.executable, str(helper), address, str(service.root), "recover"],
                    stdout=log,
                    stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    await wait_state(handle, PipelineState.awaiting_reconciliation)
                    assert service.ledger.list_operations(plan.task_id)[0].state == "outcome_unknown"
                    await handle.execute_update("reconcile")
                    result = await asyncio.wait_for(handle.result(), 30)
                    assert result.state == PipelineState.succeeded
                    with sqlite3.connect(service.root / "provider.sqlite") as db:
                        assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
                    assert len(service.ledger.list_operations(plan.task_id)) == 1
                    history = await handle.fetch_history()
                    (tmp_path / "crash-history.json").write_text(history.to_json(), encoding="utf-8")
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)

    asyncio.run(scenario())


def test_temporal_provider_error_redaction_and_reconciliation(tmp_path):
    from letsaigc.pipelines.contracts import Capability

    class Disconnect(SimulationBackend):
        capability = Capability(id="simulation.generate", resource="local-gpu")

        def __init__(self, path):
            super().__init__(path)
            self.calls = 0

        def submit(self, key, arguments):
            self.calls += 1
            super().submit(key, arguments)
            raise RuntimeError("https://fake.test?token=FAKE_SECRET_SENTINEL#PRIVATE_FRAGMENT")

    async def scenario():
        backend = Disconnect(tmp_path / "provider.sqlite")
        service = PipelineService(tmp_path / "domain", backends={"simulation.generate": backend})
        plan = service.smoke_plan("redaction")
        async with await environment(tmp_path / "temporal.sqlite") as env:
            with ThreadPoolExecutor(4) as executor:
                async with worker(env.client, service, executor):
                    handle = await begin(env.client, plan)
                    await wait_state(handle, PipelineState.awaiting_approval)
                    await approved(handle, service, plan)
                    await wait_state(handle, PipelineState.awaiting_reconciliation)
                    history = await handle.fetch_history()
                    assert "FAKE_SECRET_SENTINEL" not in history.to_json()
                    assert "PRIVATE_FRAGMENT" not in history.to_json()
                    await handle.execute_update("reconcile")
                    result = await asyncio.wait_for(handle.result(), 30)
                    assert result.state == PipelineState.succeeded
                    assert backend.calls == 1
                    assert "FAKE_SECRET_SENTINEL" not in (
                        service.root / "tasks" / plan.task_id / "manifest.json"
                    ).read_text(encoding="utf-8")

    asyncio.run(scenario())


def test_temporal_projection_fault_does_not_repeat_generation(tmp_path, monkeypatch):
    import letsaigc.execution.temporal.activities as module

    original = module.project
    failures = []

    def flaky(service, run):
        if run.state == PipelineState.succeeded and len(failures) < 3:
            failures.append(run.projection_sequence)
            raise OSError("projection disk temporarily unavailable")
        return original(service, run)

    monkeypatch.setattr(module, "project", flaky)

    async def scenario():
        service = PipelineService(tmp_path / "domain")
        plan = service.smoke_plan("projection-fault")
        async with await environment(tmp_path / "temporal.sqlite") as env:
            with ThreadPoolExecutor(4) as executor:
                async with worker(env.client, service, executor):
                    handle = await begin(env.client, plan)
                    await wait_state(handle, PipelineState.awaiting_approval)
                    await approved(handle, service, plan)
                    result = await asyncio.wait_for(handle.result(), 30)
                    assert result.state == PipelineState.succeeded
                    assert len(failures) == 3
                    with sqlite3.connect(service.root / "simulation.sqlite") as db:
                        assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
                    assert len(result.artifacts) == 1

    asyncio.run(scenario())


@workflow.defn(name="letsaigc.smoke.v1", sandboxed=False)
class Incompatible:
    @workflow.run
    async def run(self, argument: WorkflowInput) -> PipelineRun:
        await workflow.sleep(100)
        return argument.run


def test_temporal_replayer_rejects_incompatible_code(tmp_path):
    from temporalio.workflow import NondeterminismError

    async def scenario():
        service = PipelineService(tmp_path / "domain")
        plan = service.smoke_plan("replay-break")
        async with await environment(tmp_path / "temporal.sqlite") as env:
            with ThreadPoolExecutor(4) as executor:
                async with worker(env.client, service, executor):
                    handle = await begin(env.client, plan)
                    await wait_state(handle, PipelineState.awaiting_approval)
                    await approved(handle, service, plan)
                    await asyncio.wait_for(handle.result(), 30)
                    history = await handle.fetch_history()
                    with pytest.raises(NondeterminismError):
                        await Replayer(
                            workflows=[Incompatible], data_converter=pydantic_data_converter
                        ).replay_workflow(history)

    asyncio.run(scenario())


def test_temporal_server_and_worker_restart_preserves_approval_wait(tmp_path):
    async def scenario():
        service = PipelineService(tmp_path / "domain")
        plan = service.smoke_plan("restart")
        env = await environment(tmp_path / "temporal.sqlite")
        address = env.client.service_client.config.target_host
        with ThreadPoolExecutor(4) as executor:
            async with worker(env.client, service, executor):
                handle = await begin(env.client, plan)
                await wait_state(handle, PipelineState.awaiting_approval)
            await env.shutdown()
            restarted = await environment(tmp_path / "temporal.sqlite", port=int(address.rsplit(":", 1)[1]))
            async with restarted:
                async with worker(restarted.client, PipelineService(service.root), executor):
                    handle = restarted.client.get_workflow_handle(plan.task_id, result_type=PipelineRun)
                    await wait_state(handle, PipelineState.awaiting_approval)
                    assert service.ledger.list_operations(plan.task_id) == []
                    await approved(handle, service, plan)
                    result = await asyncio.wait_for(handle.result(), 30)
                    assert result.state == PipelineState.succeeded
                    assert len(service.ledger.list_operations(plan.task_id)) == 1

    asyncio.run(scenario())


def test_temporal_reject_and_cancel_without_submission(tmp_path):
    async def scenario():
        service = PipelineService(tmp_path / "domain")
        async with await environment(tmp_path / "temporal.sqlite") as env:
            with ThreadPoolExecutor(4) as executor:
                async with worker(env.client, service, executor):
                    for decision in ["reject", "cancel", "native-cancel"]:
                        plan = service.smoke_plan(decision)
                        handle = await begin(env.client, plan)
                        await wait_state(handle, PipelineState.awaiting_approval)
                        if decision == "reject":
                            receipt = approve(service.ledger, plan.task_id, plan.fingerprint, reject=True)
                            await handle.execute_update("approval", receipt)
                        elif decision == "cancel":
                            await handle.execute_update("cancel")
                        else:
                            await handle.cancel()
                        result = await asyncio.wait_for(handle.result(), 30)
                        assert result.state.value in {"cancelled", "rejected"}
                        assert not service.ledger.list_operations(plan.task_id)

    asyncio.run(scenario())
