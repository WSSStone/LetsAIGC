"""Local decomposition projection; estimated assets retain their raw evidence."""

import json

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, canonical_json, operation_id
from ..schemas.ui import UIAnalysisRequest, UISegmentationRequest, UISelection
from ..vision.segmentation import SegmentationBundle
from .resources import check_storage
from .text_assets import extract_glyph_assets


def finalize_decomposition(service, root, child, artifacts):
    request = UIAnalysisRequest.model_validate_json(service.artifacts.read(
        ArtifactRef.model_validate(root.parameters["request_ref"])))
    segmentation = UISegmentationRequest.model_validate_json(service.artifacts.read(
        ArtifactRef.model_validate(child.parameters["request_ref"])))
    refs = [ref for ref in artifacts if ref.role == "segmentation"]
    if len(refs) != 1:
        raise PipelineError("invalid_output")
    bundle = SegmentationBundle.model_validate_json(service.artifacts.read(refs[0]))
    if (bundle.task_id, bundle.operation_id, bundle.canonical_ref, bundle.selection_ref,
            bundle.selection_hash, bundle.selection_revision, bundle.model_snapshot_ref) != (
        child.task_id, operation_id(child, "segment", 0), segmentation.canonical_ref,
        segmentation.selection_ref, segmentation.selection_hash, segmentation.selection_revision,
        segmentation.model_snapshot_ref,
    ):
        raise PipelineError("input_changed")
    for item in bundle.items:
        if item.canonical_sha256 != segmentation.canonical_ref.sha256:
            raise PipelineError("input_changed")
        for ref in (item.rect_crop_ref, item.contour_mask_ref, item.estimated_alpha_ref, item.visible_crop_ref):
            if ref.task_id != child.task_id or ref.operation_id != bundle.operation_id:
                raise PipelineError("artifact_scope")
            service.artifacts.read(ref)
    glyphs = []
    selection = UISelection.model_validate_json(service.artifacts.read(segmentation.selection_ref))
    layout_ref = selection.sources[0].layout_ref
    layout = json.loads(service.artifacts.read(layout_ref)) if layout_ref else None
    if request.text_assets and layout:
        text_elements = [item for item in layout.get("elements", [])
                         if item.get("base_type", item.get("kind")) == "text"]
        # Extract only selected regions. Text requests do not widen the frozen
        # selection or create additional segmentation/model work.
        elements = {item["element_id"]: item for item in layout.get("elements", [])}
        targets = [tuple(region.xyxy) if region.kind == "bbox"
                   else tuple(elements[region.element_id]["bbox"])
                   for region in selection.sources[0].target_regions]
        prompt_boxes = [tuple(prompt.box) for prompt in segmentation.prompts]
        for element in text_elements:
            box = element.get("bbox")
            if not box or not any(a <= box[0] < box[2] <= c and b <= box[1] < box[3] <= d
                                  for a, b, c, d in targets):
                continue
            if not any(a <= box[0] < box[2] <= c and b <= box[1] < box[3] <= d
                       for a, b, c, d in prompt_boxes):
                continue
            if len(glyphs) >= 64:
                raise PipelineError("resource_insufficient")
            check_storage(service.artifacts, child.task_id, request.resources,
                          memory_bytes=(box[2] - box[0]) * (box[3] - box[1]) * 20)
            text_id = element.get("element_id", element.get("id"))
            contour = next((item.contour_mask_ref for item in bundle.items if item.element_id == text_id), None)
            glyphs.append(extract_glyph_assets(
                service.artifacts, segmentation.canonical_ref, {"text_id": text_id, "bbox": box},
                operation_id="glyph-" + bundle.operation_id, segmentation_mask_ref=contour,
            ))
    payload = {
        "schema_version": 1, "task_id": root.task_id, "output_mode": "decompose",
        "root_fingerprint": root.fingerprint, "child_fingerprint": child.fingerprint,
        "selection_ref": segmentation.selection_ref, "canonical_ref": segmentation.canonical_ref,
        "segmentation_ref": refs[0], "assets": bundle.items,
        "glyphs": [{key: value.model_dump(mode="json") if isinstance(value, ArtifactRef) else value
                    for key, value in glyph.items()} for glyph in glyphs],
        "origin": "human_assisted" if layout_ref and layout_ref.role == "review_layout" else "automatic",
        "quality_status": "pending", "asset_status": bundle.status,
    }
    # Pydantic values are converted before the bounded manifest is persisted.
    payload["assets"] = [item.model_dump(mode="json") for item in bundle.items]
    for key in ("selection_ref", "canonical_ref", "segmentation_ref"):
        payload[key] = payload[key].model_dump(mode="json")
    index = service.artifacts.put(root.task_id, "editing-output", canonical_json(payload).encode(), role="manifest",
                                  source_ids=[ref.artifact_id for ref in artifacts])
    return [*artifacts, index]
