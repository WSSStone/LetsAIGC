"""Generic Temporal entrypoints route UI editing through the root workflow."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from PIL import Image
from test_ui_editing_entrypoints import invoke
from test_ui_editing_entrypoints import reviewed as reviewed_fixture

from letsaigc.execution.temporal import client as temporal_client
from letsaigc.execution.temporal.config import TemporalConfig
from letsaigc.execution.temporal.ui_messages import UIEditingWorkflowInput, UIWorkflowInput
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.agent import TaskBudget
from letsaigc.schemas.ui import UIAnalysisRequest


@pytest.fixture
def reviewed(tmp_path, monkeypatch):
    return reviewed_fixture.__wrapped__(tmp_path, monkeypatch)


class CaptureClient:
    def __init__(self):
        self.started = None
        self.handle = CaptureHandle()

    async def start_workflow(self, name, argument, **kwargs):
        self.started = (name, argument, kwargs)
        return SimpleNamespace(id=kwargs["id"], first_execution_run_id="first-run")

    def get_workflow_handle(self, task_id):
        self.handle.task_id = task_id
        return self.handle


class CaptureHandle:
    def __init__(self):
        self.task_id = None
        self.update = None

    async def execute_update(self, name, request, **kwargs):
        self.update = (name, request, kwargs)
        return "registered_pending_validation"


def capture_connect(monkeypatch, capture):
    async def connect(_config):
        return capture

    monkeypatch.setattr(temporal_client, "connect", connect)


def test_start_routes_parse_to_existing_ui_workflow(tmp_path, monkeypatch):
    service = PipelineService(tmp_path / "parse", ui_schema=5)
    content = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(content, format="PNG")
    original = service.artifacts.put("parse-root", "input", content.getvalue(), role="original", media_type="image/png")
    plan = service.ui_plan(
        "parse-root",
        UIAnalysisRequest(
            input={"kind": "manual", "inputs": [original]},
            output_mode="parse",
            selection_mode="none",
            budget=TaskBudget(
                max_total_cost_usd=1,
                max_iteration_cost_usd=1,
                max_total_gpu_minutes=0,
                max_iteration_gpu_minutes=0,
                max_revisions=2,
            ),
        ),
    )
    capture = CaptureClient()
    capture_connect(monkeypatch, capture)

    result = asyncio.run(temporal_client.start(service, plan.task_id, TemporalConfig()))

    assert result["task_id"] == plan.task_id
    name, argument, _ = capture.started
    assert name == "letsaigc.ui.analysis.v1"
    assert isinstance(argument, UIWorkflowInput)


def _fresh_editing_service(tmp_path):
    service = PipelineService(tmp_path / "fresh", ui_schema=5)
    content = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(content, format="PNG")
    original = service.artifacts.put("fresh-root", "input", content.getvalue(), role="original", media_type="image/png")
    budget = TaskBudget(
        max_total_cost_usd=1,
        max_iteration_cost_usd=1,
        max_total_gpu_minutes=1,
        max_iteration_gpu_minutes=1,
        max_revisions=2,
    )
    return service, service.ui_plan(
        "fresh-root",
        UIAnalysisRequest(
            input={"kind": "manual", "inputs": [original]},
            output_mode="reconstruct",
            reconstruction_target="scene_background",
            selection_mode="deferred",
            budget=budget,
        ),
    )


def test_start_fresh_editing_starts_at_root_analysis(tmp_path, monkeypatch):
    service, plan = _fresh_editing_service(tmp_path)
    capture = CaptureClient()
    capture_connect(monkeypatch, capture)

    asyncio.run(temporal_client.start(service, plan.task_id, TemporalConfig()))

    name, argument, _ = capture.started
    assert name == "letsaigc.ui.editing.v1"
    assert isinstance(argument, UIEditingWorkflowInput)
    assert argument.phase_index == 0
    assert argument.approved is False
    assert argument.child is None
    assert argument.initial_child_approval is None


def test_start_reused_review_editing_skips_root_analysis_and_child_approval(reviewed, monkeypatch):
    service, _, budget = reviewed
    from letsaigc.pipelines.migrations import migrate_ui_ledger

    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    planned = invoke("plan", "--reviewed-task", "reviewed-source", "--mode", "decompose", "--budget", str(budget))
    plan = service.ledger.plan(planned["task_id"])
    capture = CaptureClient()
    capture_connect(monkeypatch, capture)

    asyncio.run(temporal_client.start(service, plan.task_id, TemporalConfig()))

    name, argument, _ = capture.started
    assert name == "letsaigc.ui.editing.v1"
    assert isinstance(argument, UIEditingWorkflowInput)
    assert argument.phase_index == 7
    assert argument.approved is True
    assert argument.child is None
    assert argument.initial_child_approval is None


def test_start_rejects_independent_editing_child(reviewed, monkeypatch):
    service, _, budget = reviewed
    from letsaigc.pipelines.migrations import migrate_ui_ledger

    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    planned = invoke("plan", "--reviewed-task", "reviewed-source", "--mode", "decompose", "--budget", str(budget))
    child_id = planned["editing"]["pending_approvals"][0]["task_id"]

    with pytest.raises(PipelineError) as caught:
        asyncio.run(temporal_client.start(service, child_id, TemporalConfig()))

    assert caught.value.code == "child_workflow_root_required"
    assert planned["task_id"] in str(caught.value)


def test_child_decision_sends_exact_receipt_to_root_workflow(reviewed, monkeypatch):
    service, _, budget = reviewed
    from letsaigc.pipelines.migrations import migrate_ui_ledger

    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    planned = invoke("plan", "--reviewed-task", "reviewed-source", "--mode", "decompose", "--budget", str(budget))
    child_id = planned["editing"]["pending_approvals"][0]["task_id"]
    child = service.ledger.plan(child_id)
    capture = CaptureClient()
    capture_connect(monkeypatch, capture)

    asyncio.run(temporal_client.decision(service, child_id, child.fingerprint, TemporalConfig()))

    assert capture.handle.task_id == planned["task_id"]
    name, request, _ = capture.handle.update
    assert name == "approval"
    assert request.task_id == child_id
    assert request.plan_fingerprint == child.fingerprint


def test_v4_editing_start_requires_migration(reviewed):
    service, _, budget = reviewed
    planned = invoke("plan", "--reviewed-task", "reviewed-source", "--mode", "decompose", "--budget", str(budget))
    plan = service.ledger.plan(planned["task_id"])

    with pytest.raises(PipelineError) as caught:
        asyncio.run(temporal_client.start(service, plan.task_id, TemporalConfig()))

    assert caught.value.code == "migration_required"
