"""SAM2 prompt execution and canonical asset materialization.

The module contains only image and array operations.  It deliberately does
not import Torch or Transformers; those objects are supplied by
``SAM2Engine`` after its local snapshot checks have completed.
"""

from __future__ import annotations

import io
import json
import time
from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageOps
from pydantic import Field

from ..assets.store import ArtifactStore
from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, Digest, Identifier, PipelineModel, canonical_json
from ..schemas.ui import UISelection

_PNG = "image/png"
_READY_THRESHOLD = 0.5


class SegmentationAsset(PipelineModel):
    """One frozen prompt and all recoverable image assets it produced."""

    schema_version: Literal[1] = 1
    element_id: Identifier
    bbox: tuple[int, int, int, int]
    status: Literal["ready", "low_confidence", "unknown"]
    reason: str = Field(min_length=1, max_length=128)
    confidence: float | None = Field(default=None, ge=0, le=1)
    canonical_sha256: Digest
    rect_crop_ref: ArtifactRef
    contour_mask_ref: ArtifactRef
    estimated_alpha_ref: ArtifactRef
    visible_crop_ref: ArtifactRef


class SegmentationBundle(PipelineModel):
    """The small, resumable result stored by the loopback service."""

    schema_version: Literal[1] = 1
    capability: Literal["segmentation"] = "segmentation"
    status: Literal["ready", "partial", "unknown"]
    task_id: Identifier
    operation_id: Identifier
    canonical_ref: ArtifactRef
    canonical_sha256: Digest
    selection_ref: ArtifactRef
    selection_revision: int = Field(ge=0, strict=True)
    selection_hash: Digest
    model_snapshot_ref: ArtifactRef
    model_digest: Digest
    items: list[SegmentationAsset] = Field(min_length=1, max_length=64)


def _error(code: str, message: str = "SAM segmentation failed") -> PipelineError:
    return PipelineError(code, message)


def _png_bytes(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=False)
    return stream.getvalue()


def _canonical_image(store: ArtifactStore, ref: ArtifactRef) -> Image.Image:
    try:
        with Image.open(io.BytesIO(store.read(ref))) as source:
            return ImageOps.exif_transpose(source).copy()
    except Exception:
        raise _error("artifact_changed", "Canonical image is unavailable") from None


def _layout_boxes(store: ArtifactStore, selection_source, canonical_ref: ArtifactRef, width: int, height: int):
    """Load a trusted layout using the shared floor/ceil normalization."""

    if selection_source.layout_ref is None:
        return {}, None
    # Kept in a tiny local adapter so the SAM module uses the exact selection
    # rules without exposing a second layout parser.
    try:
        from ..ui_analysis.selection import TrustedSource, _layout_elements, _load_layout

        trusted = TrustedSource(
            selection_source.source_id,
            canonical_ref,
            width=width,
            height=height,
            layout_ref=selection_source.layout_ref,
            canonical_ref=canonical_ref,
        )
        layout = _load_layout(
            store,
            selection_source.layout_ref,
            task_id=canonical_ref.task_id,
            source=trusted,
        )
        return _layout_elements(layout), layout
    except PipelineError:
        raise
    except Exception:
        raise _error("selection_conflict", "Frozen segmentation layout is unavailable") from None


def _contains(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def _selection_geometry(
    store: ArtifactStore, canonical_ref: ArtifactRef, selection_ref: ArtifactRef, image: Image.Image
):
    try:
        selection = UISelection.model_validate_json(store.read(selection_ref))
    except Exception:
        raise _error("selection_conflict", "Frozen segmentation selection is invalid") from None
    if len(selection.sources) != 1:
        raise _error("selection_conflict", "Segmentation accepts exactly one selected source")
    source = selection.sources[0]
    elements, _layout = _layout_boxes(store, source, canonical_ref, image.width, image.height)
    target_boxes: list[tuple[int, int, int, int]] = []
    target_element_ids: set[str] = set()
    for region in source.target_regions:
        if region.kind == "element":
            if region.element_id not in elements:
                raise _error("selection_conflict", "Selection target element is missing")
            box = elements[region.element_id]
            target_element_ids.add(region.element_id)
        else:
            box = tuple(region.xyxy)
        if not _contains((0, 0, image.width, image.height), box):
            raise _error("selection_conflict", "Selection target is outside the canonical image")
        target_boxes.append(box)
    keep_boxes = {element_id: elements[element_id] for element_id in source.keep_elements if element_id in elements}
    if len(keep_boxes) != len(source.keep_elements):
        raise _error("selection_conflict", "Selection keep element is missing")
    return selection, source, elements, target_boxes, target_element_ids, keep_boxes


def _validate_prompts(job, image: Image.Image, geometry):
    selection, source, elements, target_boxes, target_element_ids, keep_boxes = geometry
    from ..ui_analysis.selection import _covered_by_union

    if len(job.prompts) > 64 or not job.prompts:
        raise _error("invalid_input", "SAM accepts one to 64 prompts")
    seen: set[str] = set()
    checked = []
    bounds = (0, 0, image.width, image.height)
    for prompt in job.prompts:
        if prompt.element_id in seen:
            raise _error("input_changed", "SAM prompt element IDs must be unique")
        seen.add(prompt.element_id)
        box = tuple(prompt.box)
        if not _contains(bounds, box) or not _covered_by_union(box, target_boxes):
            raise _error("input_changed", "SAM prompt is outside the frozen selection")
        if prompt.element_id in keep_boxes:
            raise _error("input_changed", "SAM cannot prompt a frozen keep element")
        if prompt.element_id in elements:
            expected = elements[prompt.element_id]
            if expected != box or not any(_contains(target, expected) for target in target_boxes):
                raise _error("input_changed", "SAM prompt differs from the frozen layout element")
        elif elements:
            # Element selections have stable IDs; a made-up ID cannot hide a
            # prompt inside another selected element.
            raise _error("input_changed", "SAM prompt element is not in the frozen layout")
        for point in prompt.points:
            if not (box[0] <= point[0] < box[2] and box[1] <= point[1] < box[3]):
                raise _error("input_changed", "SAM prompt point is outside its frozen box")
        checked.append((prompt, box))
    return checked


def _to_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value)


def _first_output(outputs: Any, name: str):
    if isinstance(outputs, Mapping):
        return outputs.get(name)
    return getattr(outputs, name, None)


def _move_to_cuda(value: Any) -> Any:
    if hasattr(value, "to"):
        return value.to("cuda")
    if isinstance(value, Mapping):
        return {key: _move_to_cuda(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_move_to_cuda(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_move_to_cuda(item) for item in value)
    return value


def _logit_mask(value: Any, size: tuple[int, int]) -> np.ndarray:
    array = _to_numpy(value)
    while array.ndim > 2:
        singleton = next((axis for axis, length in enumerate(array.shape) if length == 1), None)
        if singleton is None:
            raise _error("segmentation_failed", "SAM returned more than one mask")
        array = np.take(array, 0, axis=singleton)
    if array.ndim != 2 or array.shape != size:
        raise _error("segmentation_failed", "SAM returned a mask with an unexpected canonical shape")
    if not np.isfinite(array).all():
        raise _error("segmentation_failed", "SAM returned a non-finite mask")
    return 1.0 / (1.0 + np.exp(-np.clip(array.astype(np.float32, copy=False), -40, 40)))


def _probability_mask(value: Any, size: tuple[int, int]) -> np.ndarray:
    """Validate a mask that is already in [0, 1] probability space."""

    array = _to_numpy(value)
    while array.ndim > 2:
        singleton = next((axis for axis, length in enumerate(array.shape) if length == 1), None)
        if singleton is None:
            raise _error("segmentation_failed", "SAM returned more than one mask")
        array = np.take(array, 0, axis=singleton)
    if array.ndim != 2 or array.shape != size or not np.isfinite(array).all():
        raise _error("segmentation_failed", "SAM returned an invalid canonical probability mask")
    return np.clip(array.astype(np.float32, copy=False), 0.0, 1.0)


def _confidence(outputs: Any) -> float | None:
    value = _first_output(outputs, "iou_scores")
    if value is None:
        return None
    try:
        score = float(_to_numpy(value).reshape(-1)[0])
    except (TypeError, ValueError, IndexError):
        return None
    return score if 0 <= score <= 1 else None


def render_segmentation_assets(
    image: Image.Image, mask: Any, bbox: tuple[int, int, int, int]
) -> dict[str, bytes]:
    """Render one mask into the four stable image roles.

    The mask must already be in canonical dimensions.  This prevents a
    second, silent resize after SAM2's ``post_process_masks`` mapping.
    """

    canonical = image
    rgba = image.convert("RGBA")
    soft = _probability_mask(mask, (canonical.height, canonical.width))
    contour = Image.fromarray((soft > 0.5).astype(np.uint8) * 255, mode="L")
    alpha = Image.fromarray(np.rint(soft * 255).astype(np.uint8), mode="L")
    rect = canonical.crop(bbox)
    visible = rgba.crop(bbox)
    original_alpha = np.asarray(rgba.getchannel("A"), dtype=np.float32) / 255.0
    crop_alpha = original_alpha[bbox[1] : bbox[3], bbox[0] : bbox[2]]
    crop_soft = soft[bbox[1] : bbox[3], bbox[0] : bbox[2]]
    visible_alpha = np.rint(crop_alpha * crop_soft * (crop_soft > 0.5) * 255).astype(np.uint8)
    visible.putalpha(Image.fromarray(visible_alpha, mode="L"))
    return {
        "rect_crop": _png_bytes(rect),
        "contour_mask": _png_bytes(contour),
        "estimated_alpha": _png_bytes(alpha),
        "visible_crop": _png_bytes(visible),
    }


def _predict(engine: Any, image: Image.Image, prompt, size: tuple[int, int]) -> tuple[np.ndarray, float | None]:
    boxes = [[[int(value) for value in prompt.box]]]
    inputs: dict[str, Any] = {
        "images": image.convert("RGB"),
        "input_boxes": boxes,
        "return_tensors": "pt",
    }
    if prompt.points:
        inputs["input_points"] = [[[[float(point[0]), float(point[1])] for point in prompt.points]]]
        inputs["input_labels"] = [[[1 for _ in prompt.points]]]
    try:
        encoded = engine.processor(**inputs)
        model_inputs = dict(encoded.items()) if hasattr(encoded, "items") else dict(encoded)
        original_sizes = model_inputs.pop("original_sizes", None)
        model_inputs.pop("reshaped_input_sizes", None)
        model_inputs["multimask_output"] = False
        inference_mode = getattr(getattr(engine, "_torch", None), "inference_mode", None)
        context = inference_mode() if callable(inference_mode) else nullcontext()
        with context:
            outputs = engine.model(**_move_to_cuda(model_inputs))
        raw_masks = _first_output(outputs, "pred_masks")
        if raw_masks is None:
            raise _error("segmentation_failed", "SAM returned no masks")
        post_process = getattr(engine.processor, "post_process_masks", None)
        if callable(post_process):
            original = [(size[0], size[1])] if original_sizes is None else original_sizes
            processed = post_process(raw_masks, original_sizes=original, mask_threshold=0.0, binarize=False)
            raw_masks = processed[0] if isinstance(processed, (list, tuple)) else processed
        return _logit_mask(raw_masks, size), _confidence(outputs)
    except PipelineError:
        raise
    except Exception:
        raise _error("segmentation_failed", "SAM inference failed") from None


def _asset(
    store: ArtifactStore,
    image: Image.Image,
    job,
    prompt,
    bbox: tuple[int, int, int, int],
    mask: np.ndarray,
    confidence: float | None,
    reason: str,
    status: Literal["ready", "low_confidence", "unknown"],
    used_bytes: int,
) -> tuple[SegmentationAsset, int]:
    rendered = render_segmentation_assets(image, mask, bbox)
    limit = job.resources.temporary_bytes
    per_file = job.resources.max_image_bytes
    total = sum(len(data) for data in rendered.values())
    if any(len(data) > per_file for data in rendered.values()) or used_bytes + total > limit:
        raise _error("output_limit", "SAM segmentation assets exceed the approved storage budget")
    refs = {}
    source_ids = list(dict.fromkeys([job.canonical_ref.artifact_id, job.selection_ref.artifact_id]))
    for role, data in rendered.items():
        refs[role] = store.put(
            job.task_id,
            job.operation_id,
            data,
            role=role,
            media_type=_PNG,
            source_ids=source_ids,
        )
        used_bytes += len(data)
    return (
        SegmentationAsset(
            element_id=prompt.element_id,
            bbox=bbox,
            status=status,
            reason=reason,
            confidence=confidence,
            canonical_sha256=job.canonical_ref.sha256,
            rect_crop_ref=refs["rect_crop"],
            contour_mask_ref=refs["contour_mask"],
            estimated_alpha_ref=refs["estimated_alpha"],
            visible_crop_ref=refs["visible_crop"],
        ),
        used_bytes,
    )


def _zero_asset(store, image, job, prompt, bbox, reason, used_bytes):
    zero = np.zeros((image.height, image.width), dtype=np.float32)
    return _asset(store, image, job, prompt, bbox, zero, None, reason, "unknown", used_bytes)


def segment_sam_job(engine: Any, store: ArtifactStore, job: Any) -> dict[str, Any]:
    """Run frozen prompts and publish only recoverable artifact references."""

    if job.canonical_ref.task_id != job.task_id or job.canonical_ref.role != "canonical":
        raise _error("artifact_scope", "SAM canonical input is outside the task")
    if job.selection_ref.task_id != job.task_id or job.selection_ref.role != "selection":
        raise _error("artifact_scope", "SAM selection input is outside the task")
    if job.model_snapshot_ref.task_id != job.task_id or job.model_snapshot_ref.role != "model_snapshot":
        raise _error("artifact_scope", "SAM model snapshot is outside the task")
    image = _canonical_image(store, job.canonical_ref)
    if image.width * image.height > job.resources.max_pixels:
        raise _error("input_limit", "Canonical image exceeds the approved pixel budget")
    geometry = _selection_geometry(store, job.canonical_ref, job.selection_ref, image)
    checked = _validate_prompts(job, image, geometry)
    items: list[SegmentationAsset] = []
    used_bytes = 0
    from ..ui_analysis.resources import check_storage

    deadline = time.monotonic() + job.resources.active_seconds
    estimated_full = min(job.resources.max_image_bytes, image.width * image.height * 4 + 4096)
    estimated_crop = min(job.resources.max_image_bytes, max(1, image.width * image.height) * 4 + 4096)
    estimated_item = estimated_full * 3 + estimated_crop
    for prompt, bbox in checked:
        if time.monotonic() >= deadline:
            raise _error("resource_insufficient", "SAM active execution deadline exceeded")
        check_storage(
            store,
            job.task_id,
            job.resources,
            extra_bytes=estimated_item,
            memory_bytes=image.width * image.height * 8,
        )
        try:
            mask, confidence = _predict(engine, image, prompt, (image.height, image.width))
            if time.monotonic() >= deadline:
                raise _error("resource_insufficient", "SAM active execution deadline exceeded")
            area = int(np.count_nonzero(mask > 0.5))
            if area == 0:
                item, used_bytes = _asset(
                    store, image, job, prompt, bbox, mask, None, "sam_mask_empty", "unknown", used_bytes
                )
            elif confidence is None or confidence < _READY_THRESHOLD:
                item, used_bytes = _asset(
                    store,
                    image,
                    job,
                    prompt,
                    bbox,
                    mask,
                    confidence,
                    "sam_confidence_unavailable" if confidence is None else "sam_confidence_low",
                    "low_confidence",
                    used_bytes,
                )
            else:
                item, used_bytes = _asset(
                    store, image, job, prompt, bbox, mask, confidence, "sam_mask_ready", "ready", used_bytes
                )
        except PipelineError as exc:
            if exc.code not in {"segmentation_failed"}:
                raise
            item, used_bytes = _zero_asset(store, image, job, prompt, bbox, exc.code, used_bytes)
        items.append(item)
    if all(item.status == "ready" for item in items):
        status = "ready"
    elif any(item.status != "unknown" for item in items):
        status = "partial"
    else:
        status = "unknown"
    bundle = SegmentationBundle(
        status=status,
        task_id=job.task_id,
        operation_id=job.operation_id,
        canonical_ref=job.canonical_ref,
        canonical_sha256=job.canonical_ref.sha256,
        selection_ref=job.selection_ref,
        selection_revision=job.selection_revision,
        selection_hash=job.selection_hash,
        model_snapshot_ref=job.model_snapshot_ref,
        model_digest=job.model_digest,
        items=items,
    )
    # Validate the serialized form because this is the exact service payload
    # later persisted in a single segmentation output artifact.
    return json.loads(canonical_json(bundle))
