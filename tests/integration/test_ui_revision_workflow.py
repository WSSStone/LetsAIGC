"""Offline T029 local-revision wiring through the root editing workflow.

The provider boundary in this file is a small in-memory OCR backend.  It
returns a model suggestion as a child artifact; no review artifact is mutated
and no real OCR/VLM, Temporal server, network, or GPU is used.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.contracts import Capability, Submission
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef, Cost, canonical_json, digest
from letsaigc.schemas.ui import ImageView, UIAnalysisRequest, UIObservation
from letsaigc.ui_analysis.editing import EditingExecution
from letsaigc.ui_analysis.revision import RevisionRequest
from letsaigc.ui_analysis.revision_execution import RevisionExecution

BUDGET = {
    "max_total_cost_usd": 0.5,
    "max_iteration_cost_usd": 0.5,
    "max_total_gpu_minutes": 0,
    "max_iteration_gpu_minutes": 0,
    "max_revisions": 2,
}


def _png(size=(48, 32)):
    stream = BytesIO()
    Image.new("RGB", size, "#435466").save(stream, format="PNG")
    return stream.getvalue()


def _root(tmp_path):
    service = PipelineService(tmp_path / "service", ui_schema=5)
    original = service.artifacts.put(
        "root", "input", _png(), role="original", media_type="image/png"
    )
    canonical = service.artifacts.put(
        "root", "normalize", _png(), role="canonical", media_type="image/png"
    )
    ocr_model = service.artifacts.put("root", "model", b'{"model":"fake-ocr"}', role="model")
    layout = service.artifacts.put(
        "root", "layout", canonical_json({
            "schema_version": 1,
            "source_id": original.artifact_id,
            "width": 48,
            "height": 32,
            "elements": [{"element_id": "element-1", "kind": "text", "bbox": [2, 2, 18, 10]}],
        }).encode(), role="layout"
    )
    selection = {
        "schema_version": 1,
        "sources": [{
            "source_id": original.artifact_id,
            "original_sha256": original.sha256,
            "layout_ref": layout.model_dump(mode="json"),
            "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 48, 32]}],
            "keep_elements": [],
            "remove_elements": [],
        }],
    }
    selection_ref = service.artifacts.put(
        "root", "selection", canonical_json(selection).encode(), role="selection"
    )
    request = UIAnalysisRequest(
        input={"kind": "manual", "inputs": [original]},
        output_mode="reconstruct",
        reconstruction_target="scene_background",
        selection_mode="bound",
        selection_ref=selection_ref,
        allow_local_revision=True,
        budget=BUDGET,
        model_bindings={"canonical_ref": canonical, "ocr": ocr_model, "layout_ref": layout},
    )
    root = service.ui_plan("root", request)
    service.ledger.consume_approval(approve(service.ledger, root.task_id, root.fingerprint))

    view = ImageView(
        view_id="root-view",
        kind="overview",
        canonical_ref=canonical,
        input_ref=canonical,
        crop=(0, 0, 48, 32),
        width=48,
        height=32,
        forward=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        inverse=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    )
    view_ref = service.artifacts.put(
        "root", "ocr", view.model_dump_json().encode(), role="view_manifest"
    )
    texts_value = {
        "schema_version": 1,
        "canonical_sha256": canonical.sha256,
        "texts": [{
            "text_id": "text-1",
            "text": "PLAY",
            "score": 0.9,
            "polygon": [[2, 2], [18, 2], [18, 10], [2, 10]],
        }],
    }
    texts_ref = service.artifacts.put(
        "root", "ocr", canonical_json(texts_value).encode(), role="texts"
    )
    analysis_ref = service.artifacts.put(
        "root", "analyze", canonical_json({"correction_suggestions": []}).encode(), role="analysis"
    )
    review_texts_ref = service.artifacts.put(
        "root", "review", canonical_json({"confirmed_revision": 3, "texts": texts_value["texts"]}).encode(),
        role="review_texts",
    )
    _finish_root_step(service, root, "normalize", "ui.normalize", [original, canonical])
    _finish_root_step(service, root, "ocr", "ui.ocr", [view_ref, texts_ref])
    _finish_root_step(service, root, "analyze", "ui.analyze", [view_ref, texts_ref, analysis_ref])
    _finish_root_step(service, root, "layout", "ui.layout", [layout])
    return (
        service,
        root,
        selection_ref,
        texts_ref,
        review_texts_ref,
        service.artifacts.read(texts_ref),
        service.artifacts.read(review_texts_ref),
    )


def _finish_root_step(service, root, step_id, capability, outputs):
    request = UIAnalysisRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(root.parameters["request_ref"]))
    )
    from letsaigc.schemas.ui import UIStepBinding

    binding = UIStepBinding(
        task_id=root.task_id,
        step_id=step_id,
        capability=capability,
        inputs=list(outputs),
    )
    operation = service.ledger.reserve(
        root, step_id, 0, Cost(), ui_binding=binding, ui_request=request
    )
    persisted = [
        service.artifacts.put(
            root.task_id,
            operation.operation_id,
            service.artifacts.read(ref),
            role=ref.role,
            media_type=ref.media_type,
            source_ids=[ref.artifact_id],
        )
        for ref in outputs
    ]
    service.ledger.finish_ui(
        operation.operation_id,
        Cost(),
        {"artifacts": [ref.model_dump(mode="json") for ref in persisted]},
    )


class FakeLocalOCR:
    capability = Capability(id="ui.ocr")

    def __init__(self, store):
        self.store = store
        self.submit_calls = 0
        self.calls: list[tuple[str, str]] = []
        self.outputs = {}

    def submit(self, operation_id, arguments):
        from letsaigc.schemas.ui import UIStepBinding

        binding = UIStepBinding.model_validate(arguments["binding"])
        view_ref = next(ref for ref in binding.inputs if ref.role == "view_manifest")
        view = ImageView.model_validate_json(self.store.read(view_ref))
        self.submit_calls += 1
        self.calls.append((binding.step_id, binding.task_id))
        fresh_id = "text-" + digest([view.canonical_ref.sha256, view.view_id, "PLAY!", 0])[:32]
        self.outputs[operation_id] = {
            "schema_version": 1,
            "status": "observed",
            "canonical_sha256": view.canonical_ref.sha256,
            "view_id": view.view_id,
            "raw": {
                "rec_texts": ["PLAY!"],
                "rec_scores": [0.8],
                "rec_polys": [[[0, 0], [16, 0], [16, 8], [0, 8]]],
                "dt_polys": [],
            },
            "texts": [{
                "text_id": fresh_id,
                "text": "PLAY!",
                "score": 0.8,
                "polygon": [[2, 2], [18, 2], [18, 10], [2, 10]],
                "view_id": view.view_id,
                "model_id": "fake-ocr",
                "model_version": "test",
                "status": "observed",
                "merged_view_ids": [view.view_id],
            }],
        }
        return Submission(request_id="local-ocr-request", metadata={"operation_id": operation_id})

    def inspect(self, submission):
        return UIObservation(state="succeeded", actual=Cost(cost_usd=0.4))

    def collect(self, submission):
        payload = canonical_json(self.outputs[submission.metadata["operation_id"]]).encode()
        return [("texts", payload, "application/json")]


def _revision(service, root, action="reread_text", base_revision=0):
    revision = RevisionRequest(
        base_task_id=root.task_id,
        base_fingerprint=root.fingerprint,
        base_revision=base_revision,
        action=action,
        target_ids=["text-1"],
        parameters={},
    )
    ref = service.artifacts.put(
        root.task_id,
        "revision",
        revision.model_dump_json().encode(),
        role="revision_request",
    )
    return revision, ref


def _successful_output_refs(service, task_id):
    return [
        ArtifactRef.model_validate(value)
        for operation in service.ledger.list_operations(task_id)
        if operation.state == "succeeded"
        for value in operation.result.get("artifacts", [])
    ]


def test_local_revision_planner_and_root_workflow_preserve_ref_budget_and_review(tmp_path, monkeypatch):
    (
        service,
        root,
        _selection_ref,
        root_texts_ref,
        review_texts_ref,
        root_texts_before,
        review_texts_before,
    ) = _root(tmp_path)
    revision, _revision_ref = _revision(service, root)
    planner = RevisionExecution(service)
    projection = planner.plan(revision)
    assert projection.root_task_id == root.task_id
    revision_ref = projection.revision_ref
    assert projection.impact.closure.roots == ["ocr"]
    assert projection.impact.proposal_only is True

    prepared = planner.prepare(root, revision_ref)
    assert prepared.root_task_id == root.task_id
    assert prepared.child is not None
    assert prepared.child.workflow_type == "ui_text_revision"
    child = prepared.child
    assert child.parameters["root_task_id"] == root.task_id
    approval = approve(service.ledger, child.task_id, child.fingerprint)
    service.ledger.consume_approval(approval)

    backend = FakeLocalOCR(service.artifacts)
    service.backends["ui.ocr"] = backend
    adapters = []
    from temporalio import activity, workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from letsaigc.execution.temporal.activities import PipelineActivities
    from letsaigc.execution.temporal.ui_activities import UIActivities
    from letsaigc.execution.temporal.ui_editing_activities import UIEditingActivities
    from letsaigc.execution.temporal.ui_editing_workflow import UIEditingWorkflow
    from letsaigc.execution.temporal.ui_messages import UIEditingWorkflowInput

    adapters.extend(PipelineActivities(service).registered())
    adapters.extend(UIActivities(service).registered())
    adapters.extend(UIEditingActivities(service).registered())
    registered = {activity._Definition.must_from_callable(fn).name: fn for fn in adapters}
    activity_refs = []

    async def execute_activity(name, argument, **kwargs):
        activity_refs.append((name, getattr(argument, "revision_ref", None)))
        return registered[name](argument)

    async def wait_condition(condition, timeout=None):
        assert condition()

    monkeypatch.setattr(activity, "heartbeat", lambda *args: None)
    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(
        workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id=root.task_id, run_id="revision-run", is_continue_as_new_suggested=lambda: False
        ),
    )
    monkeypatch.setattr(workflow, "all_handlers_finished", lambda: True)
    monkeypatch.setattr(workflow, "now", lambda: datetime.now(UTC))
    monkeypatch.setattr(workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(workflow, "sleep", lambda *_args, **_kwargs: asyncio.sleep(0))

    class Continued(BaseException):
        def __init__(self, argument):
            self.argument = argument

    monkeypatch.setattr(workflow, "continue_as_new", lambda argument: (_ for _ in ()).throw(Continued(argument)))

    async def run():
        SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(UIEditingWorkflow))
        argument = UIEditingWorkflowInput(
            plan=root,
            approved=True,
            phase_index=7,
            child=child,
            child_approved=True,
            revision_ref=revision_ref,
            poll_seconds=0.01,
            observations_per_run=1,
        )
        try:
            await UIEditingWorkflow().run(argument)
        except Continued as event:
            return event.argument
        raise AssertionError("workflow did not continue as new")

    continuation = asyncio.run(run())
    assert continuation.plan.task_id == root.task_id
    assert continuation.revision_ref == revision_ref
    assert continuation.run.steps[0].step_id == "ocr"
    assert continuation.run.steps[0].state == "succeeded"
    assert backend.submit_calls == 1
    edit_refs = [(name, ref) for name, ref in activity_refs if name.startswith("ui.edit.")]
    assert {name for name, _ in edit_refs} == {
        "ui.edit.submit.v1", "ui.edit.observe.v1", "ui.edit.collect.v1",
    }
    assert all(ref == revision_ref for _, ref in edit_refs)

    assert service.artifacts.read(root_texts_ref) == root_texts_before
    assert service.artifacts.read(review_texts_ref) == review_texts_before
    assert not any(ref.role == "review_texts" for ref in continuation.run.artifacts if ref.task_id == child.task_id)
    assert service.ledger.usage(root.task_id, include_children=True)["actual"] == Cost(cost_usd=0.4)
    finished = planner.prepare(root, revision_ref)
    manifest_ref = next(ref for ref in finished.artifacts if ref.role == "revision_manifest")
    manifest = json.loads(service.artifacts.read(manifest_ref))
    assert finished.state == "succeeded"
    assert manifest["proposal_only"] is True
    assert manifest["suggestions"][0]["state"] == "read_only"
    assert manifest["suggestions"][0]["actions"][0]["text"] == "PLAY!"

    # A second explicit local revision remains in the same root group.  Its
    # reservation sees the previous actual usage and cannot reopen the old root budget.
    second, second_ref = _revision(service, root, base_revision=1)
    second_projection = planner.plan(second)
    second_prepared = planner.prepare(root, second_projection.revision_ref)
    assert second_prepared.child is not None
    second_approval = approve(service.ledger, second_prepared.child.task_id, second_prepared.child.fingerprint)
    service.ledger.consume_approval(second_approval)
    second_binding = EditingExecution(service).binding(second_prepared.child)
    with pytest.raises(PipelineError) as caught:
        service.submit_step(second_prepared.child, second_binding, Cost(cost_usd=0.2))
    assert caught.value.code == "budget_insufficient"
    assert backend.submit_calls == 1


def test_review_revision_reuses_frozen_normalize_and_ocr_refs(tmp_path):
    service, root, *_ = _root(tmp_path)
    revision, _ = _revision(service, root, action="review_region")
    revision = revision.model_copy(update={
        "target_ids": ["element-1"],
        "parameters": {"user_notes": "inspect the panel"},
    })
    projection = RevisionExecution(service).plan(revision)
    recomputed = {item.artifact_ref.artifact_id for item in projection.impact.recompute}
    outputs = _successful_output_refs(service, root.task_id)
    canonical = next(ref for ref in outputs if ref.role == "canonical")
    texts = next(ref for ref in outputs if ref.role == "texts")
    assert canonical.artifact_id not in recomputed
    assert texts.artifact_id not in recomputed
