"""A new explicit revision restarts the same root workflow with its frozen ref."""

import asyncio
from types import SimpleNamespace

import pytest
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from test_ui_revision_cli import _root_fixture

from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.errors import PipelineError
from letsaigc.ui_analysis import cli
from letsaigc.ui_analysis.revision_execution import RevisionExecution


@pytest.mark.parametrize("existing", [False, True])
def test_revision_transport_preserves_root_identity_and_rejects_another_active_revision(
    tmp_path, monkeypatch, existing,
):
    service, root = _root_fixture(tmp_path, monkeypatch)
    prepared = RevisionExecution(service).plan({
        "base_task_id": root.task_id, "base_fingerprint": root.fingerprint,
        "base_revision": 0, "action": "reread_text", "target_ids": ["text-1"],
    })
    child = prepared.child
    receipt = approve(service.ledger, child.task_id, child.fingerprint)
    calls = []

    class Handle:
        id = root.task_id

        async def query(self, name, **kwargs):
            if name == "state":
                return SimpleNamespace(state=SimpleNamespace(value="awaiting_approval"))
            assert name == "revision"
            return None  # The root's initial editing flow is still active.

        async def execute_update(self, *args, **kwargs):
            pytest.fail("A revision approval reached another active revision")

    class Client:
        async def start_workflow(self, name, argument, **kwargs):
            calls.append((name, argument, kwargs))
            if existing:
                raise WorkflowAlreadyStartedError(root.task_id, name)
            return Handle()

        def get_workflow_handle(self, task_id):
            assert task_id == root.task_id
            return Handle()

    async def connect(_config):
        return Client()

    monkeypatch.setattr(cli.temporal, "connect", connect)
    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(
        poll_seconds=1, observations_per_run=4, activity_timeout_seconds=60, task_queue="test",
    ))
    coroutine = cli.start_approved(service, root, receipt, revision_ref=prepared.revision_ref)
    if existing:
        with pytest.raises(PipelineError) as caught:
            asyncio.run(coroutine)
        assert caught.value.code == "revision_in_progress"
    else:
        asyncio.run(coroutine)
    name, argument, options = calls[0]
    assert name == "letsaigc.ui.editing.v1"
    assert options["id"] == root.task_id
    assert options["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE
    assert argument.revision_ref == prepared.revision_ref
    assert argument.initial_child_approval == receipt
    assert argument.phase_index == 7
    assert not service.ledger.list_operations(child.task_id)
