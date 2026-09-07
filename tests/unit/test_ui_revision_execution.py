"""Provider-free tests for bounded T029 revision planning."""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from letsaigc.pipelines.contracts import Capability, Submission
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ApprovalRequest, ArtifactRef, Cost, canonical_json
from letsaigc.schemas.ui import UIAnalysisRequest, UIObservation, UISegmentationRequest
from letsaigc.ui_analysis.editing import EditingExecution
from letsaigc.ui_analysis.revision import RevisionRequest
from letsaigc.ui_analysis.revision_execution import RevisionExecution
from letsaigc.ui_analysis.revision_segmentation import merge_revision_segmentation
from letsaigc.ui_analysis.selection import record_selection
from letsaigc.vision.segmentation import SegmentationAsset, SegmentationBundle


def _png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (48, 32), (40, 70, 90)).save(stream, format="PNG")
    return stream.getvalue()


def _root(tmp_path, *, gpu=False, mode="reconstruct"):
    service = PipelineService(tmp_path / "service", ui_schema=5)
    original = service.artifacts.put("root", "input", _png(), role="original", media_type="image/png")
    canonical = service.artifacts.put("root", "normalize", _png(), role="canonical", media_type="image/png")
    layout = service.artifacts.put(
        "root",
        "layout",
        canonical_json(
            {
                "schema_version": 1,
                "source_id": original.artifact_id,
                "canonical_sha256": canonical.sha256,
                "canonical_ref": canonical.model_dump(mode="json"),
                "width": 48,
                "height": 32,
                "elements": [
                    {
                        "element_id": "element-1",
                        "kind": "text",
                        "bbox": [2, 2, 18, 10],
                        "text_region_ids": ["text-1"],
                    },
                    {
                        "element_id": "element-2",
                        "kind": "text",
                        "bbox": [22, 2, 38, 10],
                        "text_region_ids": ["text-2"],
                    },
                ],
            }
        ).encode(),
        role="layout",
    )
    texts = service.artifacts.put(
        "root",
        "ocr",
        canonical_json(
            {
                "schema_version": 1,
                "canonical_sha256": canonical.sha256,
                "texts": [
                    {
                        "text_id": "text-1",
                        "text": "PLAY",
                        "score": 0.9,
                        "polygon": [[2, 2], [18, 2], [18, 10], [2, 10]],
                    },
                    {
                        "text_id": "text-2",
                        "text": "MAP",
                        "score": 0.9,
                        "polygon": [[22, 2], [38, 2], [38, 10], [22, 10]],
                    },
                ],
            }
        ).encode(),
        role="texts",
    )
    selection = service.artifacts.put(
        "root",
        "selection",
        canonical_json(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "source_id": original.artifact_id,
                        "original_sha256": original.sha256,
                        "layout_ref": layout.model_dump(mode="json"),
                        "target_regions": [
                            {"kind": "element", "element_id": "element-1"},
                            {"kind": "element", "element_id": "element-2"},
                        ],
                        "keep_elements": [],
                        "remove_elements": [],
                    }
                ],
            }
        ).encode(),
        role="selection",
    )
    ocr = service.artifacts.put("root", "model", b"ocr-model", role="model")
    request = UIAnalysisRequest(
        input={"kind": "manual", "inputs": [original]},
        output_mode=mode,
        reconstruction_target="scene_background" if mode == "reconstruct" else None,
        selection_mode="bound",
        selection_ref=selection,
        allow_local_revision=True,
        budget={
            "max_total_cost_usd": 1.0,
            "max_iteration_cost_usd": 0.5,
            "max_total_gpu_minutes": 4 if gpu else 0,
            "max_iteration_gpu_minutes": 2 if gpu else 0,
            "max_revisions": 2,
        },
        model_bindings={"canonical_ref": canonical, "layout_ref": layout, "texts_ref": texts, "ocr": ocr},
    )
    root = service.ui_plan("root", request)
    return service, root


class _ReadySAM:
    capability = Capability(id="ui.segment", resource="local-gpu")

    def __init__(self, service):
        self.service = service
        self.submit_calls = 0

    def submit(self, operation_id, _arguments):
        self.submit_calls += 1
        return Submission(request_id="sam-" + operation_id, metadata={"operation_id": operation_id})

    def inspect(self, _submission):
        return UIObservation(state="succeeded", actual=Cost(gpu_minutes=1))

    def collect(self, submission):
        operation = self.service.ledger.get(submission.metadata["operation_id"])
        plan = self.service.ledger.plan(operation.task_id)
        request = UISegmentationRequest.model_validate_json(
            self.service.artifacts.read(ArtifactRef.model_validate(plan.parameters["request_ref"]))
        )
        image = Image.open(io.BytesIO(self.service.artifacts.read(request.canonical_ref))).convert("RGB")
        items = []
        for prompt in request.prompts:
            mask = Image.new("L", image.size, 0)
            for y in range(prompt.box[1], prompt.box[3]):
                for x in range(prompt.box[0], prompt.box[2]):
                    mask.putpixel((x, y), 255)
            crop = image.crop(prompt.box).convert("RGBA")
            crop.putalpha(Image.new("L", crop.size, 255))
            refs = {
                "rect_crop": self.service.artifacts.put(
                    plan.task_id,
                    submission.metadata["operation_id"],
                    _png_image(crop),
                    role="rect_crop",
                    media_type="image/png",
                ),
                "contour_mask": self.service.artifacts.put(
                    plan.task_id,
                    submission.metadata["operation_id"],
                    _png_image(mask),
                    role="contour_mask",
                    media_type="image/png",
                ),
                "estimated_alpha": self.service.artifacts.put(
                    plan.task_id,
                    submission.metadata["operation_id"],
                    _png_image(mask),
                    role="estimated_alpha",
                    media_type="image/png",
                ),
                "visible_crop": self.service.artifacts.put(
                    plan.task_id,
                    submission.metadata["operation_id"],
                    _png_image(crop),
                    role="visible_crop",
                    media_type="image/png",
                ),
            }
            items.append(
                SegmentationAsset(
                    element_id=prompt.element_id,
                    bbox=prompt.box,
                    status="ready",
                    reason="test",
                    confidence=1,
                    canonical_sha256=request.canonical_ref.sha256,
                    rect_crop_ref=refs["rect_crop"],
                    contour_mask_ref=refs["contour_mask"],
                    estimated_alpha_ref=refs["estimated_alpha"],
                    visible_crop_ref=refs["visible_crop"],
                )
            )
        bundle = SegmentationBundle(
            status="ready",
            task_id=plan.task_id,
            operation_id=submission.metadata["operation_id"],
            canonical_ref=request.canonical_ref,
            canonical_sha256=request.canonical_ref.sha256,
            selection_ref=request.selection_ref,
            selection_revision=request.selection_revision,
            selection_hash=request.selection_ref.sha256,
            model_snapshot_ref=request.model_snapshot_ref,
            model_digest=request.model_snapshot_ref.sha256,
            items=items,
        )
        return [("segmentation", canonical_json(bundle).encode(), "application/json")]

    def release_operation(self, _submission):
        return {"released": True, "device": "cuda"}


class _ReadyInpaint(_ReadySAM):
    capability = Capability(id="ui.inpaint", resource="local-gpu")

    def collect(self, _submission):
        return [("image", _png(), "image/png")]


def _png_image(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=False)
    return stream.getvalue()


def _approve_and_complete(service, plan, backend):
    approval = ApprovalRequest(
        request_id="approve-" + plan.task_id,
        task_id=plan.task_id,
        plan_fingerprint=plan.fingerprint,
        decision="approve",
    )
    service.ledger.record_approval(approval, actor="unit-test")
    service.ledger.consume_approval(approval)
    service.backends[backend.capability.id] = backend
    editing = EditingExecution(service)
    operation = service.submit_step(plan, editing.binding(plan), Cost(gpu_minutes=1))
    service.observe_step(plan, operation.operation_id)
    return service.collect_step(plan, operation.operation_id)


def _request(root, action, target_ids, parameters=None):
    return RevisionRequest(
        base_task_id=root.task_id,
        base_fingerprint=root.fingerprint,
        base_revision=0,
        action=action,
        target_ids=target_ids,
        parameters=parameters or {},
    )


def test_local_revision_actions_are_child_scoped_and_repeatable(tmp_path):
    service, root = _root(tmp_path)
    execution = RevisionExecution(service)
    cases = (
        ("reread_text", ["text-1"], {}),
        ("review_region", ["element-1"], {"user_notes": "check the label"}),
    )
    for action, targets, parameters in cases:
        revision = _request(root, action, targets, parameters)
        first = execution.plan(revision)
        second = execution.plan(revision)
        assert first.revision_ref == second.revision_ref
        assert first.child == second.child
        assert first.child is not None
        assert (
            first.child.workflow_type
            == {
                "reread_text": "ui_text_revision",
                "review_region": "ui_region_revision",
            }[action]
        )
        assert first.child.envelope.budget.max_iteration_gpu_minutes == 0
        assert all(ref.task_id == first.child.task_id for ref in first.child.inputs)
        wrapper = json.loads(service.artifacts.read(first.child.inputs[0]))
        assert wrapper["action"] == action
        assert wrapper["selection_ref"]["task_id"] == first.child.task_id


def test_revision_plan_persists_fixed_child_identity_fields(tmp_path):
    service, root = _root(tmp_path)
    revision = _request(root, "reread_text", ["text-1"])
    prepared = RevisionExecution(service).plan(revision)
    payload = json.loads(service.artifacts.read(prepared.revision_ref))
    assert payload["kind"] == "ui_revision_plan"
    assert payload["root_task_id"] == root.task_id
    assert payload["child_task_id"] == prepared.child.task_id
    assert payload["child_fingerprint"] == prepared.child.fingerprint
    assert payload["request_ref"]["role"] == "revision_request"


@pytest.mark.parametrize("mode", ["reconstruct", "decompose"])
def test_adjust_segmentation_creates_exact_new_child_and_reuses_untouched_assets(tmp_path, mode):
    service, root = _root(tmp_path, gpu=True, mode=mode)
    snapshot = service.artifacts.put("root", "sam-model", b"sam", role="model_snapshot")
    editing = EditingExecution(service)
    initial = editing.prepare(root, model_snapshot_ref=snapshot)
    assert initial.child is not None
    segment = initial.child
    base_result = _approve_and_complete(service, segment, _ReadySAM(service))

    revision = _request(
        segment,
        "adjust_segmentation",
        ["element-1"],
        {"prompts": [{"element_id": "element-1", "box": [3, 3, 17, 9]}]},
    )
    execution = RevisionExecution(service)
    planned = execution.plan(revision)
    repeated = execution.plan(revision)
    assert planned.child is not None
    assert planned.child.task_id != segment.task_id
    assert planned.child == repeated.child
    request = UISegmentationRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(planned.child.parameters["request_ref"]))
    )
    assert {prompt.element_id for prompt in request.prompts} == {"element-1"}
    assert next(prompt for prompt in request.prompts if prompt.element_id == "element-1").box == (3, 3, 17, 9)
    assert all(ref.task_id == planned.child.task_id for ref in planned.child.inputs)
    new_result = _approve_and_complete(service, planned.child, _ReadySAM(service))
    merged = merge_revision_segmentation(
        service,
        segment,
        planned.child,
        [ArtifactRef.model_validate(value) for value in new_result.result["artifacts"]],
    )
    merged_ref = next(ref for ref in merged if ref.role == "segmentation" and len(ref.source_ids) >= 2)
    from letsaigc.vision.segmentation import SegmentationBundle

    base_bundle = SegmentationBundle.model_validate_json(
        service.artifacts.read(
            ArtifactRef.model_validate(
                next(ref for ref in base_result.result["artifacts"] if ref["role"] == "segmentation")
            )
        )
    )
    merged_bundle = SegmentationBundle.model_validate_json(service.artifacts.read(merged_ref))
    untouched_base = next(item for item in base_bundle.items if item.element_id == "element-2")
    untouched_merged = next(item for item in merged_bundle.items if item.element_id == "element-2")
    assert untouched_merged.rect_crop_ref.artifact_id == untouched_base.rect_crop_ref.artifact_id
    assert untouched_merged.rect_crop_ref.task_id == planned.child.task_id
    assert any(ref == merged_ref for ref in merged)
    advanced = execution.prepare(root, planned.revision_ref)
    if mode == "reconstruct":
        assert advanced.state == "awaiting_approval"
        assert advanced.child is not None and advanced.child.workflow_type == "ui_inpaint"
        _approve_and_complete(service, advanced.child, _ReadyInpaint(service))
        finished = execution.prepare(root, planned.revision_ref)
        assert finished.state == "succeeded"
        assert any(ref.role == "revision_manifest" for ref in finished.artifacts)
        assert execution.prepare(root, planned.revision_ref) == finished
    else:
        assert advanced.state == "succeeded"
        assert any(ref.role == "revision_manifest" for ref in advanced.artifacts)
        assert len([ref for ref in advanced.artifacts if ref.role == "segmentation"]) == 1


def test_regenerate_creates_new_masked_child_and_terminal_manifest(tmp_path):
    service, root = _root(tmp_path, gpu=True)
    snapshot = service.artifacts.put("root", "sam-model", b"sam", role="model_snapshot")
    editing = EditingExecution(service)
    initial = editing.prepare(root, model_snapshot_ref=snapshot)
    assert initial.child is not None
    segment = initial.child
    _approve_and_complete(service, segment, _ReadySAM(service))
    inpaint_preparation = editing.prepare(root)
    assert inpaint_preparation.child is not None
    initial_inpaint = inpaint_preparation.child
    initial_request = ArtifactRef.model_validate(initial_inpaint.parameters["request_ref"])
    from letsaigc.schemas.agent import MaskedGenerationPlan

    initial_masked = MaskedGenerationPlan.model_validate_json(service.artifacts.read(initial_request))
    _approve_and_complete(service, initial_inpaint, _ReadyInpaint(service))

    revision = _request(
        initial_inpaint,
        "regenerate",
        ["element-1"],
        {"prompt": "restore the panel", "seed": 9},
    )
    execution = RevisionExecution(service)
    planned = execution.plan(revision)
    assert planned.child is not None
    assert planned.child.task_id != initial_inpaint.task_id
    regenerated_request = ArtifactRef.model_validate(planned.child.parameters["request_ref"])
    regenerated = MaskedGenerationPlan.model_validate_json(service.artifacts.read(regenerated_request))
    assert regenerated.parameters["prompt"] == "restore the panel"
    assert regenerated.parameters["seed"] == 9
    assert regenerated.model == initial_masked.model
    assert regenerated.recipe == initial_masked.recipe
    assert regenerated.image_mask.mask_ref.sha256 == initial_masked.image_mask.mask_ref.sha256
    _approve_and_complete(service, planned.child, _ReadyInpaint(service))
    finished = execution.prepare(root, planned.revision_ref)
    assert finished.state == "succeeded"
    manifest = next(ref for ref in finished.artifacts if ref.role == "revision_manifest")
    payload = json.loads(service.artifacts.read(manifest))
    assert payload["child_task_id"] == planned.child.task_id
    assert payload["suggestions"][0]["kind"] == "revision_candidate"


def test_revision_rejects_completed_child_after_selection_supersession(tmp_path):
    service, root = _root(tmp_path, gpu=True)
    snapshot = service.artifacts.put("root", "sam-model", b"sam", role="model_snapshot")
    editing = EditingExecution(service)
    initial = editing.prepare(root, model_snapshot_ref=snapshot)
    assert initial.child is not None
    segment = initial.child
    _approve_and_complete(service, segment, _ReadySAM(service))
    root_request = UIAnalysisRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(root.parameters["request_ref"]))
    )
    selection_value = json.loads(service.artifacts.read(root_request.selection_ref))
    selection_value["sources"][0]["target_regions"] = [{"kind": "element", "element_id": "element-2"}]
    replacement = service.artifacts.put(
        "root",
        "selection-replacement",
        canonical_json(selection_value).encode(),
        role="selection",
    )
    # The catalog replacement is the stale-selection signal; the child still
    # has a succeeded provider operation and therefore cannot be re-planned.
    record_selection(service.artifacts, root.task_id, replacement, revision=1)
    revision = _request(
        segment,
        "adjust_segmentation",
        ["element-1"],
        {"prompts": [{"element_id": "element-1", "box": [3, 3, 17, 9]}]},
    )
    try:
        RevisionExecution(service).plan(revision)
    except PipelineError as exc:
        assert exc.code in {"selection_superseded", "source_scope", "artifact_scope"}
    else:
        raise AssertionError("a stale succeeded child must not create a revision child")
