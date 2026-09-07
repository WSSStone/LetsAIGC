"""Acceptance checks for the immutable decomposition projection."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

import pytest
from PIL import Image, ImageDraw

from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.agent import TaskBudget
from letsaigc.schemas.pipeline import ArtifactRef, canonical_json, operation_id
from letsaigc.schemas.ui import (
    UIAnalysisRequest,
    UIResourceLimits,
    UISegmentationRequest,
)
from letsaigc.ui_analysis.editing_outputs import finalize_decomposition
from letsaigc.vision.segmentation import SegmentationAsset, SegmentationBundle


@dataclass(frozen=True)
class DecompositionCase:
    service: PipelineService
    root: object
    child: object
    segmentation_ref: ArtifactRef
    bundle: SegmentationBundle
    canonical: ArtifactRef
    child_selection: ArtifactRef


def _png(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=False)
    return stream.getvalue()


def _budget() -> TaskBudget:
    return TaskBudget(
        max_total_cost_usd=0,
        max_iteration_cost_usd=0,
        max_total_gpu_minutes=1,
        max_iteration_gpu_minutes=1,
        max_revisions=0,
    )


def _layout(task_id: str, source_id: str) -> dict:
    return {
        "schema_version": 2,
        "task_id": task_id,
        "source_id": source_id,
        "width": 64,
        "height": 48,
        "elements": [
            {"element_id": "text-inside", "base_type": "text", "bbox": [8, 8, 22, 22]},
            {"element_id": "text-outside", "base_type": "text", "bbox": [40, 8, 58, 22]},
        ],
    }


def _make_case(tmp_path) -> DecompositionCase:
    service = PipelineService(tmp_path / "domain", ui_schema=5)
    image = Image.new("RGB", (64, 48), "white")
    draw = ImageDraw.Draw(image)
    # Two simple high-contrast glyph-like shapes make the CPU extractor
    # deterministic without relying on an installed font.
    for offset in (0, 32):
        draw.rectangle((10 + offset, 10, 12 + offset, 20), fill="black")
        draw.rectangle((18 + offset, 10, 20 + offset, 20), fill="black")
        draw.rectangle((10 + offset, 14, 20 + offset, 16), fill="black")
    image_bytes = _png(image)

    original = service.artifacts.put("root", "input", image_bytes, role="original", media_type="image/png")
    canonical_root = service.artifacts.put(
        "root", "input", image_bytes, role="canonical", media_type="image/png"
    )
    root_layout = service.artifacts.put(
        "root", "review-layout", canonical_json(_layout("root", original.artifact_id)).encode(),
        role="review_layout",
    )
    root_selection = service.artifacts.put(
        "root",
        "selection",
        canonical_json(
            {
                "schema_version": 1,
                "sources": [{
                    "source_id": original.artifact_id,
                    "original_sha256": original.sha256,
                    "layout_ref": root_layout.model_dump(mode="json"),
                    "target_regions": [{"kind": "bbox", "xyxy": [4, 4, 30, 28]}],
                }],
            }
        ).encode(),
        role="selection",
    )
    root_request = UIAnalysisRequest(
        input={"kind": "manual", "inputs": [original]},
        output_mode="decompose",
        selection_mode="bound",
        selection_ref=root_selection,
        text_assets=True,
        budget=_budget(),
        model_bindings={"canonical_ref": canonical_root, "layout_ref": root_layout},
    )
    root = service.ui_plan("root", root_request)

    child_id = "child"
    canonical = service.artifacts.put(child_id, "input", image_bytes, role="canonical", media_type="image/png")
    snapshot = service.artifacts.put(child_id, "input", b"synthetic-sam", role="model_snapshot")
    child_layout = service.artifacts.put(
        child_id, "input", canonical_json(_layout(child_id, original.artifact_id)).encode(),
        role="review_layout",
    )
    child_selection = service.artifacts.put(
        child_id,
        "input",
        canonical_json(
            {
                "schema_version": 1,
                "sources": [{
                    "source_id": original.artifact_id,
                    "original_sha256": original.sha256,
                    "layout_ref": child_layout.model_dump(mode="json"),
                    "target_regions": [{"kind": "bbox", "xyxy": [4, 4, 30, 28]}],
                }],
            }
        ).encode(),
        role="selection",
    )
    child_request = UISegmentationRequest(
        canonical_ref=canonical,
        selection_ref=child_selection,
        selection_revision=0,
        selection_hash=child_selection.sha256,
        prompts=[
            {"element_id": "text-inside", "box": [4, 4, 30, 28]},
            {"element_id": "text-outside", "box": [36, 4, 62, 28]},
        ],
        model_snapshot_ref=snapshot,
        prompt_version="synthetic-v1",
        resources=UIResourceLimits(),
        result_roles=["segmentation"],
    )
    request_ref = service.artifacts.put(
        child_id, "input", canonical_json(child_request).encode(), role="request"
    )
    child = service.ui_child_plan(
        child_id,
        parent_task_id=root.task_id,
        purpose="segmentation",
        source_ids=[original.artifact_id],
        request_ref=request_ref,
        selection_ref=root_selection,
        selection_revision=0,
        budget=_budget(),
    )

    segment_operation = operation_id(child, "segment", 0)
    assets = []
    for element_id, box, offset in (
        ("text-inside", (8, 8, 22, 22), 0),
        ("text-outside", (40, 8, 58, 22), 32),
    ):
        contour = Image.new("L", image.size, 0)
        contour_draw = ImageDraw.Draw(contour)
        contour_draw.rectangle((10 + offset, 10, 12 + offset, 20), fill=255)
        contour_draw.rectangle((18 + offset, 10, 20 + offset, 20), fill=255)
        contour_draw.rectangle((10 + offset, 14, 20 + offset, 16), fill=255)
        rect = image.crop(box)
        contour_ref = service.artifacts.put(
            child_id, segment_operation, _png(contour), role="contour_mask", media_type="image/png",
            source_ids=[canonical.artifact_id],
        )
        rect_ref = service.artifacts.put(
            child_id, segment_operation, _png(rect), role="rect_crop", media_type="image/png",
            source_ids=[canonical.artifact_id],
        )
        alpha_ref = service.artifacts.put(
            child_id, segment_operation, _png(contour), role="estimated_alpha", media_type="image/png",
            source_ids=[canonical.artifact_id, contour_ref.artifact_id],
        )
        visible_ref = service.artifacts.put(
            child_id, segment_operation, _png(rect), role="visible_crop", media_type="image/png",
            source_ids=[canonical.artifact_id, contour_ref.artifact_id],
        )
        assets.append(SegmentationAsset(
            element_id=element_id,
            bbox=box,
            status="ready",
            reason="synthetic",
            confidence=1,
            canonical_sha256=canonical.sha256,
            rect_crop_ref=rect_ref,
            contour_mask_ref=contour_ref,
            estimated_alpha_ref=alpha_ref,
            visible_crop_ref=visible_ref,
        ))
    bundle = SegmentationBundle(
        status="ready",
        task_id=child_id,
        operation_id=segment_operation,
        canonical_ref=canonical,
        canonical_sha256=canonical.sha256,
        selection_ref=child_selection,
        selection_revision=0,
        selection_hash=child_selection.sha256,
        model_snapshot_ref=snapshot,
        model_digest=snapshot.sha256,
        items=assets,
    )
    segmentation_ref = service.artifacts.put(
        child_id, segment_operation, canonical_json(bundle).encode(), role="segmentation",
        source_ids=[canonical.artifact_id, child_selection.artifact_id, snapshot.artifact_id],
    )
    return DecompositionCase(service, root, child, segmentation_ref, bundle, canonical, child_selection)


def _manifest(case: DecompositionCase, *, artifacts=None) -> dict:
    outputs = finalize_decomposition(
        case.service, case.root, case.child, [case.segmentation_ref] if artifacts is None else artifacts
    )
    manifest = next(ref for ref in outputs if ref.role == "manifest")
    return json.loads(case.service.artifacts.read(manifest))


def test_finalize_decomposition_persists_assets_and_selected_text_glyphs(tmp_path):
    case = _make_case(tmp_path)

    manifest = _manifest(case)

    assert manifest["output_mode"] == "decompose"
    assert manifest["asset_status"] == "ready"
    assert len(manifest["assets"]) == 2
    assert manifest["origin"] == "human_assisted"
    # The second text element lies outside the frozen prompt/selection box;
    # text_assets must not expand that approved region.
    assert [item["text_id"] for item in manifest["glyphs"]] == ["text-inside"]
    assert manifest["glyphs"][0]["bbox"] == [8, 8, 22, 22]


def test_finalize_decomposition_rejects_out_of_scope_segmentation_asset(tmp_path):
    case = _make_case(tmp_path)
    foreign_contour = case.service.artifacts.put(
        case.child.task_id,
        "foreign-operation",
        case.service.artifacts.read(case.bundle.items[0].contour_mask_ref),
        role="contour_mask",
        media_type="image/png",
        source_ids=[case.canonical.artifact_id],
    )
    changed_item = case.bundle.items[0].model_copy(update={"contour_mask_ref": foreign_contour})
    changed_bundle = case.bundle.model_copy(update={"items": [changed_item, *case.bundle.items[1:]]})
    changed_ref = case.service.artifacts.put(
        case.child.task_id,
        case.bundle.operation_id,
        canonical_json(changed_bundle).encode(),
        role="segmentation",
    )

    with pytest.raises(PipelineError) as raised:
        _manifest(case, artifacts=[changed_ref])
    assert raised.value.code == "artifact_scope"


def test_finalize_decomposition_rejects_selection_mismatch(tmp_path):
    case = _make_case(tmp_path)
    other_selection = case.service.artifacts.put(
        case.child.task_id,
        "other-selection",
        case.service.artifacts.read(case.child_selection) + b" ",
        role="selection",
    )
    changed_bundle = case.bundle.model_copy(update={
        "selection_ref": other_selection,
        "selection_hash": other_selection.sha256,
    })
    changed_ref = case.service.artifacts.put(
        case.child.task_id,
        case.bundle.operation_id,
        canonical_json(changed_bundle).encode(),
        role="segmentation",
    )

    with pytest.raises(PipelineError) as raised:
        _manifest(case, artifacts=[changed_ref])
    assert raised.value.code == "input_changed"
