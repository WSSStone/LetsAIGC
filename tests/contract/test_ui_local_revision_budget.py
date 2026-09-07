"""T029 local revision accounting at the existing v5 child boundary.

These tests use only synthetic artifacts and the ledger.  A local OCR/VLM
child is still an exact child approval, but its call limit and root monetary
usage belong to the original UI root.  Local suggestions do not consume the
GPU generation revision counter.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef, Cost, canonical_json
from letsaigc.schemas.ui import ImageView, UIAnalysisRequest, UICallLimits, UIResourceLimits, UIStepBinding
from letsaigc.schemas.ui_provider import ManualUIInput
from letsaigc.ui_analysis.revision import RevisionRequest

BUDGET = {
    "max_total_cost_usd": 1.0,
    "max_iteration_cost_usd": 0.6,
    "max_total_gpu_minutes": 10.0,
    "max_iteration_gpu_minutes": 5.0,
    "max_revisions": 2,
}


def _identity():
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _root(tmp_path, *, ocr_rereads=2, vlm_calls=4, vlm_local_views=3):
    service = PipelineService(tmp_path, ui_schema=5)
    image_bytes = BytesIO()
    Image.new("RGB", (64, 48), "white").save(image_bytes, format="PNG")
    image = service.artifacts.put("root", "input", image_bytes.getvalue(), role="original", media_type="image/png")
    request = UIAnalysisRequest(
        input={"kind": "manual", "inputs": [image]},
        output_mode="reconstruct",
        reconstruction_target="scene_background",
        selection_mode="deferred",
        allow_local_revision=True,
        budget=BUDGET,
        resources=UIResourceLimits(ocr_max_tiles=1, vlm_local_views=vlm_local_views),
        limits=UICallLimits(ocr_rereads_per_image=ocr_rereads, vlm_calls_per_image=vlm_calls),
    )
    root = service.ui_plan("root", request)
    service.ledger.consume_approval(approve(service.ledger, root.task_id, root.fingerprint))
    selection = {
        "schema_version": 1,
        "sources": [{
            "source_id": image.artifact_id,
            "original_sha256": image.sha256,
            "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 64, 48]}],
            "keep_elements": [],
            "remove_elements": [],
        }],
    }
    selection_ref = service.artifacts.put(
        root.task_id, "selection", canonical_json(selection).encode(), role="selection"
    )
    return service, root, image, image_bytes.getvalue(), selection_ref


def _local_child(service, root, image, image_bytes, selection_ref, task_id, action):
    """Register one T029 child using the agreed immutable wrapper fields."""
    canonical = service.artifacts.put(task_id, "input", image_bytes, role="canonical", media_type="image/png")
    view = ImageView(
        view_id="local-view",
        kind="local",
        canonical_ref=canonical,
        input_ref=canonical,
        crop=(0, 0, 64, 48),
        width=64,
        height=48,
        forward=_identity(),
        inverse=_identity(),
    )
    view_ref = service.artifacts.put(
        task_id, "input", view.model_dump_json().encode(), role="view_manifest"
    )
    texts_ref = service.artifacts.put(
        task_id,
        "input",
        canonical_json({
            "schema_version": 1,
            "canonical_sha256": canonical.sha256,
            "texts": [{
                "text_id": "text-1",
                "text": "PLAY",
                "score": 0.5,
                "polygon": [[2, 2], [20, 2], [20, 10], [2, 10]],
            }],
        }).encode(),
        role="texts",
    )
    parameters_ref = service.artifacts.put(
        task_id, "input", canonical_json({"language": "auto", "text_id": "text-1"}).encode(),
        role="parameters",
    )
    local_selection = service.artifacts.put(
        task_id, "input", service.artifacts.read(selection_ref), role="selection"
    )
    root_request = UIAnalysisRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(root.parameters["request_ref"]))
    )
    local_request = root_request.model_copy(
        update={
            "input": ManualUIInput(inputs=[canonical]),
            "selection_mode": "bound",
            "selection_ref": local_selection,
            "model_bindings": {"canonical_ref": canonical},
        }
    )
    analysis_ref = service.artifacts.put(
        task_id,
        "input",
        local_request.model_dump_json().encode(),
        role="request",
    )
    revision = RevisionRequest(
        base_task_id=root.task_id,
        base_fingerprint=root.fingerprint,
        base_revision=0,
        action=action,
        target_ids=["text-1"] if action == "reread_text" else ["element-1"],
        parameters={"user_notes": "check region"} if action == "review_region" else {},
    )
    revision_ref = service.artifacts.put(
        task_id, "input", revision.model_dump_json().encode(), role="revision_request"
    )
    wrapper = {
        "schema_version": 1,
        "action": action,
        "analysis_request_ref": analysis_ref.model_dump(mode="json"),
        "revision_request_ref": revision_ref.model_dump(mode="json"),
        "selection_ref": local_selection.model_dump(mode="json"),
        "selection_revision": 0,
        "view_ref": view_ref.model_dump(mode="json"),
        "texts_ref": texts_ref.model_dump(mode="json"),
        "parameters_ref": parameters_ref.model_dump(mode="json"),
    }
    request_ref = service.artifacts.put(
        task_id, "request", canonical_json(wrapper).encode(), role="request"
    )
    return service.ui_child_plan(
        task_id,
        parent_task_id=root.task_id,
        purpose=action,
        source_ids=[image.artifact_id],
        request_ref=request_ref,
        selection_ref=selection_ref,
        selection_revision=0,
        budget=BUDGET,
    )


def _authorize(service, child):
    service.ledger.consume_approval(approve(service.ledger, child.task_id, child.fingerprint))


def _binding(child, capability, step_id):
    selection = ArtifactRef.model_validate(child.parameters["selection_ref"])
    return UIStepBinding(
        task_id=child.task_id,
        step_id=step_id,
        capability=capability,
        inputs=list(child.inputs),
        parameters_ref=next((ref for ref in child.inputs if ref.role == "parameters"), None),
        selection_ref=selection,
        selection_revision=0,
        selection_hash=selection.sha256,
    )


def _finish(service, child, capability, step_id, cost):
    root_request = UIAnalysisRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(service.ledger.plan("root").parameters["request_ref"]))
    )
    binding = _binding(child, capability, step_id)
    operation = service.ledger.reserve(
        child,
        step_id,
        0,
        cost,
        ui_binding=binding,
        ui_request=root_request,
    )
    return service.ledger.finish_ui(operation.operation_id, cost, {"artifacts": []})


def _root_step(service, root, image, capability, step_id):
    request = UIAnalysisRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(root.parameters["request_ref"]))
    )
    binding = UIStepBinding(task_id=root.task_id, step_id=step_id, capability=capability, inputs=[image])
    operation = service.ledger.reserve(
        root, step_id, 0, Cost(), ui_binding=binding, ui_request=request
    )
    return service.ledger.finish_ui(operation.operation_id, Cost(), {"artifacts": []})


def test_local_children_share_root_cost_and_do_not_consume_gpu_revision_budget(tmp_path):
    service, root, image, image_bytes, selection_ref = _root(tmp_path, ocr_rereads=2, vlm_calls=4)
    first = _local_child(service, root, image, image_bytes, selection_ref, "reread-1", "reread_text")
    _authorize(service, first)
    _finish(service, first, "ui.ocr", "reread_text", Cost(cost_usd=0.4))

    second = _local_child(service, root, image, image_bytes, selection_ref, "review-1", "review_region")
    _authorize(service, second)
    _finish(service, second, "ui.analyze", "review_region", Cost(cost_usd=0.4))

    usage = service.ledger.usage(root.task_id, include_children=True)
    assert usage["actual"] == Cost(cost_usd=0.8)
    with service.ledger.transaction() as db:
        rows = db.execute(
            "SELECT capability,revision_units FROM ui_operation_charges WHERE root_task_id=? ORDER BY operation_id",
            (root.task_id,),
        ).fetchall()
        assert {row["capability"] for row in rows} == {"ui.ocr", "ui.analyze"}
        assert [row["revision_units"] for row in rows] == [0, 0]
        assert (
            db.execute(
                "SELECT revision_count FROM ui_budget_groups WHERE root_task_id=?",
                (root.task_id,),
            ).fetchone()[0]
            == 0
        )

    third = _local_child(service, root, image, image_bytes, selection_ref, "review-2", "review_region")
    _authorize(service, third)
    with pytest.raises(PipelineError) as caught:
        _finish(service, third, "ui.analyze", "review_region", Cost(cost_usd=0.4))
    assert caught.value.code in {"budget_insufficient", "total_budget", "iteration_budget"}


def test_local_ocr_reread_uses_root_ocr_limit(tmp_path):
    service, root, image, image_bytes, selection_ref = _root(tmp_path, ocr_rereads=1, vlm_calls=4)
    _root_step(service, root, image, "ui.ocr", "ocr.0")
    first = _local_child(service, root, image, image_bytes, selection_ref, "reread-allowed", "reread_text")
    _authorize(service, first)
    _finish(service, first, "ui.ocr", "reread_text", Cost())
    second = _local_child(service, root, image, image_bytes, selection_ref, "reread-denied", "reread_text")
    _authorize(service, second)
    with pytest.raises(PipelineError) as caught:
        _finish(service, second, "ui.ocr", "reread_text", Cost())
    assert caught.value.code == "call_limit"


def test_local_vlm_review_uses_root_vlm_limit(tmp_path):
    service, root, image, image_bytes, selection_ref = _root(tmp_path, ocr_rereads=2, vlm_calls=2)
    _root_step(service, root, image, "ui.analyze", "analyze")
    first = _local_child(service, root, image, image_bytes, selection_ref, "review-allowed", "review_region")
    _authorize(service, first)
    _finish(service, first, "ui.analyze", "review_region", Cost())
    second = _local_child(service, root, image, image_bytes, selection_ref, "review-denied", "review_region")
    _authorize(service, second)
    with pytest.raises(PipelineError) as caught:
        _finish(service, second, "ui.analyze", "review_region", Cost())
    assert caught.value.code == "call_limit"


def test_existing_gpu_child_still_requires_its_exact_approval(tmp_path):
    service, root, image, image_bytes, selection_ref = _root(tmp_path)
    # The legacy segmentation child remains a separate approval boundary.
    canonical = service.artifacts.put("segment", "input", image_bytes, role="canonical", media_type="image/png")
    local_selection = service.artifacts.put(
        "segment", "input", service.artifacts.read(selection_ref), role="selection"
    )
    snapshot = service.artifacts.put("segment", "model", b'{"fixture":true}', role="model_snapshot")
    request = {
        "schema_version": 1,
        "canonical_ref": canonical.model_dump(mode="json"),
        "selection_ref": local_selection.model_dump(mode="json"),
        "selection_revision": 0,
        "selection_hash": local_selection.sha256,
        "prompts": [{"element_id": "region-1", "box": [0, 0, 64, 48], "points": []}],
        "model_snapshot_ref": snapshot.model_dump(mode="json"),
        "prompt_version": "fixture-v1",
        "resources": UIResourceLimits().model_dump(mode="json"),
        "result_roles": ["segmentation"],
    }
    request_ref = service.artifacts.put("segment", "request", canonical_json(request).encode(), role="request")
    child = service.ui_child_plan(
        "segment", parent_task_id=root.task_id, purpose="segmentation", source_ids=[image.artifact_id],
        request_ref=request_ref, selection_ref=selection_ref, selection_revision=0, budget=BUDGET,
    )
    binding = UIStepBinding(
        task_id=child.task_id, step_id="segment", capability="ui.segment", inputs=child.inputs,
        selection_ref=ArtifactRef.model_validate(child.parameters["selection_ref"]),
        selection_revision=0, selection_hash=ArtifactRef.model_validate(child.parameters["selection_ref"]).sha256,
    )
    with pytest.raises(PipelineError) as caught:
        service.ledger.reserve(child, "segment", 0, Cost(gpu_minutes=1), ui_binding=binding)
    assert caught.value.code == "approval_required"
    _authorize(service, child)
    assert service.ledger.reserve(child, "segment", 0, Cost(gpu_minutes=1), ui_binding=binding).state == "prepared"
