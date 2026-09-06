"""Deterministic, CPU-only extraction of estimated glyph assets.

The extractor deliberately treats OCR geometry as a search region, never as
evidence that every pixel in that rectangle is a glyph.  A segmentation
contour may narrow the evidence, but it does not turn an estimated glyph into
an original transparent layer.
"""

from __future__ import annotations

import math
from array import array
from collections import deque
from collections.abc import Mapping, Sequence
from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, canonical_json

MAX_GLYPH_ROI_PIXELS = 4_000_000


def _error(code: str, message: str) -> PipelineError:
    return PipelineError(code, message)


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


def _read_image(store, ref: ArtifactRef, *, role: str | None = None) -> Image.Image:
    if role is not None and ref.role != role:
        raise _error("artifact_scope", f"Text asset requires a {role} reference")
    try:
        with Image.open(BytesIO(store.read(ref))) as loaded:
            loaded.load()
            return ImageOps.exif_transpose(loaded).copy()
    except (OSError, ValueError, SyntaxError) as exc:
        raise _error("artifact_changed", "Text asset image is unavailable") from exc


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _box(value: Any, size: tuple[int, int]) -> tuple[int, int, int, int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise _error("text_asset_invalid", "Text region bbox must contain four coordinates")
    if not all(_number(item) for item in value):
        raise _error("text_asset_invalid", "Text region bbox coordinates are invalid")
    x0, y0 = (math.floor(float(item)) for item in value[:2])
    x1, y1 = (math.ceil(float(item)) for item in value[2:])
    width, height = size
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise _error("text_asset_invalid", "Text region bbox is outside the canonical image")
    return x0, y0, x1, y1


def _region_mask(region: Mapping[str, Any], box: tuple[int, int, int, int], size: tuple[int, int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    polygon = region.get("polygon")
    if polygon is None:
        return np.ones((y1 - y0, x1 - x0), dtype=bool)
    if not isinstance(polygon, Sequence) or isinstance(polygon, (str, bytes)) or len(polygon) < 3:
        raise _error("text_asset_invalid", "Text region polygon is invalid")
    points: list[tuple[float, float]] = []
    width, height = size
    for point in polygon:
        if not isinstance(point, Sequence) or isinstance(point, (str, bytes)) or len(point) != 2:
            raise _error("text_asset_invalid", "Text region polygon point is invalid")
        if not all(_number(item) for item in point):
            raise _error("text_asset_invalid", "Text region polygon coordinate is invalid")
        px, py = (float(item) for item in point)
        if not (0 <= px <= width and 0 <= py <= height):
            raise _error("text_asset_invalid", "Text region polygon is outside the canonical image")
        points.append((px - x0, py - y0))
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return np.asarray(mask, dtype=np.uint8) > 0


def _filter_components(
    mask: np.ndarray, minimum: int
) -> tuple[np.ndarray, list[tuple[int, tuple[int, int, int, int]]]]:
    """Filter 8-connected components in one bounded pass without OpenCV."""

    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    refined = np.zeros_like(mask, dtype=bool)
    result: list[tuple[int, tuple[int, int, int, int]]] = []
    for top in range(height):
        for left in range(width):
            if not mask[top, left] or visited[top, left]:
                continue
            queue = deque([(top, left)])
            visited[top, left] = True
            size = 0
            pixels = array("I")
            min_x = max_x = left
            min_y = max_y = top
            while queue:
                row, column = queue.popleft()
                pixels.append(row * width + column)
                size += 1
                min_x, max_x = min(min_x, column), max(max_x, column)
                min_y, max_y = min(min_y, row), max(max_y, row)
                for row_delta in (-1, 0, 1):
                    for column_delta in (-1, 0, 1):
                        if not row_delta and not column_delta:
                            continue
                        next_row, next_column = row + row_delta, column + column_delta
                        if (
                            0 <= next_row < height
                            and 0 <= next_column < width
                            and mask[next_row, next_column]
                            and not visited[next_row, next_column]
                        ):
                            visited[next_row, next_column] = True
                            queue.append((next_row, next_column))
            component_box = (min_x, min_y, max_x + 1, max_y + 1)
            if size >= minimum:
                result.append((size, component_box))
                for position in pixels:
                    refined.flat[position] = True
    return refined, result


def _ref_dict(ref: ArtifactRef | None) -> dict[str, Any] | None:
    return ref.model_dump(mode="json") if ref is not None else None


def _unavailable(
    store,
    canonical_ref: ArtifactRef,
    operation_id: str,
    region: Mapping[str, Any],
    box: tuple[int, int, int, int],
    rect_crop_ref: ArtifactRef,
    contour_mask_ref: ArtifactRef | None,
    reason: str,
    *,
    uncertainties: list[str],
) -> dict[str, Any]:
    text_id = region["text_id"]
    provenance = {
        "schema_version": 1,
        "kind": "glyph_asset",
        "status": "unavailable",
        "origin": "estimated",
        "text_id": text_id,
        "canonical_ref": _ref_dict(canonical_ref),
        "contour_mask_ref": _ref_dict(contour_mask_ref),
        "rect_crop_ref": _ref_dict(rect_crop_ref),
        "source_refs": [
            _ref_dict(canonical_ref),
            *([_ref_dict(contour_mask_ref)] if contour_mask_ref else []),
            _ref_dict(rect_crop_ref),
        ],
        "bbox": list(box),
        "method": "cpu-local-contrast-connected-components",
        "epistemic_status": "estimated",
        "uncertainties": [reason, *uncertainties],
    }
    provenance_ref = store.put(
        canonical_ref.task_id,
        operation_id,
        canonical_json(provenance).encode(),
        role="provenance",
        source_ids=[
            canonical_ref.artifact_id,
            rect_crop_ref.artifact_id,
            *([contour_mask_ref.artifact_id] if contour_mask_ref else []),
        ],
    )
    return {
        "status": "unavailable",
        "text_id": text_id,
        "bbox": list(box),
        "rect_crop_ref": rect_crop_ref,
        "contour_mask_ref": contour_mask_ref,
        "glyph_mask_ref": None,
        "glyph_image_ref": None,
        "provenance_ref": provenance_ref,
        "uncertainties": [reason, *uncertainties],
    }


def extract_glyph_assets(
    store,
    canonical_ref: ArtifactRef,
    text_region: Mapping[str, Any],
    *,
    operation_id: str,
    segmentation_mask_ref: ArtifactRef | None = None,
) -> dict[str, Any]:
    """Extract estimated glyph pixels from one canonical OCR region.

    The operation is deterministic and local.  ``contour_mask`` is optional,
    but when present it must be a full-canonical, grayscale observation.  No
    model, provider, ledger, or external service is touched.
    """

    if canonical_ref.role != "canonical":
        raise _error("artifact_scope", "Glyph extraction requires a canonical image")
    if not isinstance(text_region, Mapping):
        raise _error("text_asset_invalid", "Text region must be an object")
    text_id = text_region.get("text_id")
    if not isinstance(text_id, str) or not text_id:
        raise _error("text_asset_invalid", "Text region text_id is required")
    canonical = _read_image(store, canonical_ref, role="canonical")
    if canonical.mode not in {"RGB", "RGBA", "L"}:
        canonical = canonical.convert("RGBA")
    raw_bbox = text_region.get("bbox")
    if raw_bbox is None:
        polygon = text_region.get("polygon")
        if not isinstance(polygon, Sequence) or isinstance(polygon, (str, bytes)) or len(polygon) < 3:
            raise _error("text_asset_invalid", "Text region requires a bbox or polygon")
        try:
            points = [(float(point[0]), float(point[1])) for point in polygon]
        except (IndexError, TypeError, ValueError) as exc:
            raise _error("text_asset_invalid", "Text region polygon is invalid") from exc
        if not all(_number(value) for point in points for value in point):
            raise _error("text_asset_invalid", "Text region polygon is invalid")
        raw_bbox = [
            min(point[0] for point in points),
            min(point[1] for point in points),
            max(point[0] for point in points),
            max(point[1] for point in points),
        ]
    box = _box(raw_bbox, canonical.size)
    region_mask = _region_mask(text_region, box, canonical.size)
    x0, y0, x1, y1 = box
    contour_mask = None
    if segmentation_mask_ref is not None:
        if segmentation_mask_ref.task_id != canonical_ref.task_id or segmentation_mask_ref.role != "contour_mask":
            raise _error("artifact_scope", "Glyph contour must be a task-scoped contour_mask")
        if canonical_ref.artifact_id not in segmentation_mask_ref.source_ids:
            raise _error("artifact_changed", "Glyph contour is not derived from this canonical image")
        contour_mask = _read_image(store, segmentation_mask_ref)
        if contour_mask.mode != "L" or contour_mask.size != canonical.size:
            raise _error("artifact_changed", "Glyph contour must be a full-size grayscale mask")
    rect_crop = canonical.crop(box)
    rect_crop_ref = store.put(
        canonical_ref.task_id,
        operation_id,
        _png(rect_crop),
        role="rect_crop",
        media_type="image/png",
        source_ids=[canonical_ref.artifact_id],
    )
    if rect_crop.width * rect_crop.height > MAX_GLYPH_ROI_PIXELS:
        return _unavailable(
            store,
            canonical_ref,
            operation_id,
            text_region,
            box,
            rect_crop_ref,
            segmentation_mask_ref,
            "text ROI exceeds the deterministic CPU pixel limit",
            uncertainties=["glyph extraction is bounded to a small OCR region"],
        )

    contour_array = np.asarray(contour_mask, dtype=np.uint8) if contour_mask is not None else None
    crop_array = np.asarray(rect_crop.convert("RGBA"), dtype=np.uint8)
    rgb = crop_array[:, :, :3].astype(np.float32)
    alpha = crop_array[:, :, 3] > 0
    valid = region_mask & alpha
    uncertainties = ["font shape, hidden pixels, and alpha are estimated; original pixels remain in rect_crop"]
    if not np.any(valid):
        return _unavailable(
            store,
            canonical_ref,
            operation_id,
            text_region,
            box,
            rect_crop_ref,
            segmentation_mask_ref,
            "text region contains no visible pixels",
            uncertainties=uncertainties,
        )

    border = np.zeros_like(valid, dtype=bool)
    border[0, :] = border[-1, :] = True
    border[:, 0] = border[:, -1] = True
    border &= valid
    border_pixels = rgb[border] if np.any(border) else rgb[valid]
    background = np.median(border_pixels, axis=0)
    background_distance = np.linalg.norm(border_pixels - background, axis=1)
    background_noise = float(np.median(background_distance))
    if float(np.percentile(background_distance, 90)) > 18:
        return _unavailable(
            store,
            canonical_ref,
            operation_id,
            text_region,
            box,
            rect_crop_ref,
            segmentation_mask_ref,
            "background colour is too unstable for deterministic extraction",
            uncertainties=uncertainties,
        )
    deviation = np.linalg.norm(rgb - background, axis=2)
    values = deviation[valid]
    signal = float(np.max(values))
    threshold = max(16.0, background_noise * 3.0 + 3.0)
    if signal < threshold or float(np.ptp(values)) < 3:
        return _unavailable(
            store,
            canonical_ref,
            operation_id,
            text_region,
            box,
            rect_crop_ref,
            segmentation_mask_ref,
            "text evidence has insufficient local contrast",
            uncertainties=uncertainties,
        )
    candidate = (deviation >= threshold) & valid
    quantized = np.floor(np.clip(rgb[valid], 0, 255) / 16).astype(np.uint8)
    distinct_colours = np.unique(quantized, axis=0).shape[0]
    if distinct_colours > max(32, int(quantized.shape[0] * 0.12)):
        return _unavailable(
            store,
            canonical_ref,
            operation_id,
            text_region,
            box,
            rect_crop_ref,
            segmentation_mask_ref,
            "foreground/background colours are too complex for deterministic extraction",
            uncertainties=uncertainties,
        )
    if contour_array is not None:
        candidate &= contour_array[y0:y1, x0:x1] > 0
        method = "contour-guided-background-connected-components"
    else:
        method = "rgb-background-contrast-connected-components"
    uncertainties.append("foreground colour is estimated against a near-uniform RGB background")

    coverage = float(np.count_nonzero(candidate)) / float(candidate.size)
    if coverage <= 0 or coverage >= 0.92:
        return _unavailable(
            store,
            canonical_ref,
            operation_id,
            text_region,
            box,
            rect_crop_ref,
            segmentation_mask_ref,
            "contour is empty or covers the OCR rectangle",
            uncertainties=uncertainties,
        )
    minimum = max(2, int(candidate.size * 0.0005))
    refined, component_data = _filter_components(candidate, minimum)
    if not component_data:
        return _unavailable(
            store,
            canonical_ref,
            operation_id,
            text_region,
            box,
            rect_crop_ref,
            segmentation_mask_ref,
            "no connected glyph component was found",
            uncertainties=uncertainties,
        )
    if not np.any(refined):
        return _unavailable(
            store,
            canonical_ref,
            operation_id,
            text_region,
            box,
            rect_crop_ref,
            segmentation_mask_ref,
            "glyph components are below the minimum evidence size",
            uncertainties=uncertainties,
        )

    status = "estimated"
    score = text_region.get("score", text_region.get("confidence"))
    if text_region.get("status") == "low_confidence" or (_number(score) and float(score) < 0.75):
        status = "low_confidence"
        uncertainties.append("OCR confidence is below the deterministic extraction threshold")
    if segmentation_mask_ref is None:
        uncertainties.append("local contrast may include outlines or omit faint strokes")
    mask_image = Image.fromarray(np.where(refined, 255, 0).astype(np.uint8), mode="L")
    glyph_mask_ref = store.put(
        canonical_ref.task_id,
        operation_id,
        _png(mask_image),
        role="glyph_mask",
        media_type="image/png",
        source_ids=[canonical_ref.artifact_id, *([segmentation_mask_ref.artifact_id] if segmentation_mask_ref else [])],
    )
    glyph_array = crop_array.copy()
    glyph_array[:, :, 3] = np.where(refined, 255, 0)
    glyph_array[~refined, :3] = 0
    glyph_image_ref = store.put(
        canonical_ref.task_id,
        operation_id,
        _png(Image.fromarray(glyph_array, mode="RGBA")),
        role="glyph_image",
        media_type="image/png",
        source_ids=[canonical_ref.artifact_id, *([segmentation_mask_ref.artifact_id] if segmentation_mask_ref else [])],
    )
    component_boxes = [list(component_box) for _, component_box in component_data]
    provenance = {
        "schema_version": 1,
        "kind": "glyph_asset",
        "status": status,
        "origin": "estimated",
        "text_id": text_id,
        "canonical_ref": _ref_dict(canonical_ref),
        "contour_mask_ref": _ref_dict(segmentation_mask_ref),
        "rect_crop_ref": _ref_dict(rect_crop_ref),
        "glyph_mask_ref": _ref_dict(glyph_mask_ref),
        "glyph_image_ref": _ref_dict(glyph_image_ref),
        "source_refs": [
            _ref_dict(canonical_ref),
            *([_ref_dict(segmentation_mask_ref)] if segmentation_mask_ref else []),
            _ref_dict(rect_crop_ref),
            _ref_dict(glyph_mask_ref),
            _ref_dict(glyph_image_ref),
        ],
        "bbox": list(box),
        "method": method,
        "epistemic_status": "estimated",
        "component_boxes": component_boxes,
        "uncertainties": uncertainties,
    }
    provenance_ref = store.put(
        canonical_ref.task_id,
        operation_id,
        canonical_json(provenance).encode(),
        role="provenance",
        source_ids=[
            canonical_ref.artifact_id,
            rect_crop_ref.artifact_id,
            glyph_mask_ref.artifact_id,
            glyph_image_ref.artifact_id,
            *([segmentation_mask_ref.artifact_id] if segmentation_mask_ref else []),
        ],
    )
    return {
        "status": status,
        "text_id": text_id,
        "bbox": list(box),
        "rect_crop_ref": rect_crop_ref,
        "contour_mask_ref": segmentation_mask_ref,
        "glyph_mask_ref": glyph_mask_ref,
        "glyph_image_ref": glyph_image_ref,
        "provenance_ref": provenance_ref,
        "uncertainties": uncertainties,
    }


__all__ = ["extract_glyph_assets"]
