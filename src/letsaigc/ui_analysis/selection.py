"""Trusted, provider-free UI selection preparation.

The selection document is user-facing input, while the refs and hashes in the
stored document are assigned by this module.  Nothing in this module submits
an operation or calls OCR/VLM/GPU code.  It is deliberately independent from
the later SAM/Comfy child workflow.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageCms, ImageDraw, ImageOps

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, canonical_json
from ..schemas.ui import UISelection, UISelectionCandidate

MAX_SELECTION_BYTES = 1024 * 1024


@dataclass(frozen=True)
class TrustedSource:
    """The minimum trusted facts needed to validate one selection source."""

    source_id: str
    original_ref: ArtifactRef
    width: int | None = None
    height: int | None = None
    layout_ref: ArtifactRef | None = None
    canonical_ref: ArtifactRef | None = None

    @property
    def original_sha256(self) -> str:
        return self.original_ref.sha256


@dataclass(frozen=True)
class SelectionPreparation:
    """A validated selection and its immutable artifact reference."""

    selection: UISelection
    selection_ref: ArtifactRef
    model_calls: int = 0


@dataclass(frozen=True)
class CandidatePreparation:
    candidate: UISelectionCandidate
    selection: UISelection
    model_calls: int = 0


@dataclass(frozen=True)
class SelectionCatalog:
    task_id: str
    revision: int
    state: str
    candidates: tuple[UISelectionCandidate, ...] = ()
    selection_ref: ArtifactRef | None = None
    model_calls: int = 0


def _error(code: str, message: str = "Invalid UI selection") -> PipelineError:
    return PipelineError(code, message)


def load_document(path: str | Path) -> Any:
    """Read a bounded JSON/YAML-like document supplied by the CLI.

    The CLI already handles YAML for the existing commands.  Selection files
    are intentionally JSON only so that the machine contract has one stable
    byte representation and no YAML aliases or custom tags.
    """

    path = Path(path)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise _error("invalid_selection", "Selection file is unavailable") from exc
    if len(content) > MAX_SELECTION_BYTES:
        raise _error("invalid_selection", "Selection file exceeds the size limit")
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error("invalid_selection", "Selection file must contain JSON") from exc
    return value


def source_id_for_ref(ref: ArtifactRef, index: int = 1) -> str:
    """Match the deterministic source ID used by :class:`UIIntake`."""

    if type(index) is not int or index < 1:
        raise ValueError("source index must be positive")
    return f"source-{ref.sha256[:24]}-{index}"


def _as_ref(value: TrustedSource | ArtifactRef | Mapping[str, Any]) -> ArtifactRef:
    if isinstance(value, TrustedSource):
        return value.original_ref
    if isinstance(value, ArtifactRef):
        return value
    try:
        return ArtifactRef.model_validate(value)
    except Exception as exc:
        raise _error("artifact_scope", "Selection source is not a trusted artifact") from exc


def _source_map(
    sources: Mapping[str, TrustedSource | ArtifactRef | Mapping[str, Any]]
    | Iterable[TrustedSource | ArtifactRef],
) -> dict[str, TrustedSource]:
    if isinstance(sources, Mapping):
        result: dict[str, TrustedSource] = {}
        for source_id, raw in sources.items():
            if isinstance(raw, TrustedSource):
                source = raw
                if source.source_id != source_id:
                    source = TrustedSource(
                        source_id,
                        source.original_ref,
                        source.width,
                        source.height,
                        source.layout_ref,
                        source.canonical_ref,
                    )
            else:
                ref = _as_ref(raw)
                source = TrustedSource(source_id, ref)
            result[source_id] = source
        return result
    result = {}
    for index, raw in enumerate(sources, 1):
        if isinstance(raw, TrustedSource):
            source = raw
        else:
            ref = _as_ref(raw)
            source = TrustedSource(source_id_for_ref(ref, index), ref)
        result[source.source_id] = source
    return result


def _normalized_pixels(store, ref: ArtifactRef) -> tuple[tuple[int, int], bytes]:
    """Read pixels after EXIF orientation, so canonical binding is semantic."""
    try:
        with Image.open(io.BytesIO(store.read(ref))) as image:
            normalized = ImageOps.exif_transpose(image).convert("RGBA")
            if image.info.get("icc_profile"):
                profile = ImageCms.ImageCmsProfile(io.BytesIO(image.info["icc_profile"]))
                rgb = ImageCms.profileToProfile(normalized.convert("RGB"), profile, ImageCms.createProfile("sRGB"))
                rgb.putalpha(normalized.getchannel("A"))
                normalized = rgb
            return normalized.size, normalized.tobytes()
    except (OSError, ValueError, SyntaxError) as exc:
        raise _error("invalid_selection", "Selection image is unavailable") from exc


def _dimensions(store, source: TrustedSource) -> tuple[int, int]:
    if source.width is not None and source.height is not None:
        if type(source.width) is int and type(source.height) is int and source.width > 0 and source.height > 0:
            return source.width, source.height
        raise _error("invalid_selection", "Trusted source dimensions are invalid")
    return _normalized_pixels(store, source.original_ref)[0]


def _load_layout(store, layout_ref: ArtifactRef, *, task_id: str, source: TrustedSource) -> dict[str, Any]:
    if layout_ref.task_id != task_id or layout_ref.role not in {"layout", "review_layout"}:
        raise _error("artifact_scope", "Selection layout is outside the current trusted scope")
    if source.original_ref.task_id != task_id:
        raise _error("artifact_scope", "Selection source is outside the current task")
    if source.canonical_ref is not None and (
        source.canonical_ref.task_id != task_id or source.canonical_ref.role != "canonical"
    ):
        raise _error("artifact_scope", "Trusted canonical is outside the current task")
    try:
        payload = json.loads(store.read(layout_ref))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise _error("invalid_selection", "Selection layout is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") not in {1, 2}:
        raise _error("invalid_selection", "Selection layout schema is unsupported")
    if layout_ref.role == "review_layout":
        from ..schemas.ui_review import ReviewDocument

        try:
            document = ReviewDocument.model_validate(payload)
        except ValueError as exc:
            raise _error("invalid_selection", "Confirmed review layout is invalid") from exc
        if document.task_id != task_id:
            raise _error("artifact_scope", "Confirmed review layout is outside the current task")
    if payload.get("source_id") != source.source_id:
        raise _error("selection_conflict", "Selection layout belongs to another source")
    canonical_ref = None
    if layout_ref.role == "layout":
        canonical_sha = payload.get("canonical_sha256")
        if not isinstance(canonical_sha, str) or len(canonical_sha) != 64:
            raise _error("invalid_selection", "Selection layout canonical identity is invalid")
        try:
            canonical_ref = ArtifactRef.model_validate(payload.get("canonical_ref"))
        except Exception as exc:
            raise _error("invalid_selection", "Selection layout canonical reference is invalid") from exc
        if (
            canonical_ref.task_id != task_id
            or canonical_ref.role != "canonical"
            or canonical_ref.sha256 != canonical_sha
        ):
            raise _error("selection_conflict", "Selection layout canonical reference is inconsistent")
        store.read(canonical_ref)
        trusted_canonical = source.canonical_ref or canonical_ref
        if source.canonical_ref is not None:
            store.read(source.canonical_ref)
            if source.canonical_ref != canonical_ref:
                raise _error("selection_conflict", "Selection layout is bound to another canonical image")
        original_size, original_pixels = _normalized_pixels(store, source.original_ref)
        canonical_size, canonical_pixels = _normalized_pixels(store, trusted_canonical)
        if original_size != canonical_size or original_pixels != canonical_pixels:
            raise _error("selection_conflict", "Canonical pixels do not match the trusted original")
    elif source.canonical_ref is not None:
        store.read(source.canonical_ref)
        original_size, original_pixels = _normalized_pixels(store, source.original_ref)
        canonical_size, canonical_pixels = _normalized_pixels(store, source.canonical_ref)
        if original_size != canonical_size or original_pixels != canonical_pixels:
            raise _error("selection_conflict", "Canonical pixels do not match the trusted original")
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise _error("invalid_selection", "Selection layout has no element list")
    seen: set[str] = set()
    width, height = _dimensions(store, source)
    if source.canonical_ref is not None and canonical_ref is not None and canonical_ref != source.canonical_ref:
        raise _error("selection_conflict", "Selection layout is bound to another canonical image")
    if (
        source.original_ref.role == "canonical"
        and canonical_ref is not None
        and canonical_ref.sha256 != source.original_ref.sha256
    ):
        raise _error("selection_conflict", "Selection layout is bound to another canonical image")
    if canonical_ref is not None:
        canonical_size, _ = _normalized_pixels(store, canonical_ref)
        if canonical_size != (width, height):
            raise _error("selection_conflict", "Selection layout dimensions differ from the source")
    if payload.get("width") != width or payload.get("height") != height:
        raise _error("selection_conflict", "Selection layout dimensions are stale")
    # A reviewed layout includes its canonical dimensions.  The source image
    # is the authority for bounds; a disagreement is stale/corrupt input.
    for item in elements:
        if not isinstance(item, dict):
            raise _error("invalid_selection", "Selection layout contains an invalid element")
        element_id = item.get("element_id")
        if not isinstance(element_id, str) or not element_id or element_id in seen:
            raise _error("invalid_selection", "Selection layout element IDs are invalid")
        seen.add(element_id)
        box = item.get("bbox")
        if (
            not isinstance(box, (list, tuple))
            or len(box) != 4
            or any(type(value) not in {int, float} or not math.isfinite(value) for value in box)
            or box[0] < 0
            or box[1] < 0
            or box[2] <= box[0]
            or box[3] <= box[1]
            or box[2] > width
            or box[3] > height
        ):
            raise _error("invalid_selection", "Selection layout contains an invalid bounding box")
        # Automatic model-view mapping uses floats; match the existing crop
        # and review enclosing-pixel rule without rewriting the frozen layout.
        item["bbox"] = [math.floor(box[0]), math.floor(box[1]), math.ceil(box[2]), math.ceil(box[3])]
    return payload


def _layout_elements(layout: Mapping[str, Any]) -> dict[str, tuple[int, int, int, int]]:
    return {
        item["element_id"]: tuple(item["bbox"])
        for item in layout.get("elements", [])
        if isinstance(item, dict) and isinstance(item.get("element_id"), str)
    }


def _contains(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def _covered_by_union(inner: tuple[int, int, int, int], boxes: list[tuple[int, int, int, int]]) -> bool:
    """Return whether ``inner`` is fully covered by the rectangle union."""
    if not boxes:
        return False
    x_edges = sorted({inner[0], inner[2], *[edge for box in boxes for edge in (box[0], box[2])]})
    for left, right in zip(x_edges, x_edges[1:], strict=False):
        left = max(left, inner[0])
        right = min(right, inner[2])
        if left >= right:
            continue
        intervals = sorted(
            (max(inner[1], box[1]), min(inner[3], box[3]))
            for box in boxes
            if box[0] <= left and box[2] >= right and box[1] < inner[3] and box[3] > inner[1]
        )
        cursor = inner[1]
        for top, bottom in intervals:
            if top > cursor:
                return False
            cursor = max(cursor, bottom)
            if cursor >= inner[3]:
                break
        if cursor < inner[3]:
            return False
    return True


def _region_box(
    region: Mapping[str, Any], elements: Mapping[str, tuple[int, int, int, int]]
) -> tuple[int, int, int, int]:
    if region.get("kind") == "bbox":
        box = region.get("xyxy")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise _error("invalid_selection", "Selection bbox is invalid")
        if any(type(value) is not int for value in box):
            raise _error("invalid_selection", "Selection bbox coordinates must be integers")
        result = tuple(box)
        if result[2] <= result[0] or result[3] <= result[1]:
            raise _error("invalid_selection", "Selection bbox must have positive area")
        return result
    if region.get("kind") == "element":
        element_id = region.get("element_id")
        if element_id not in elements:
            raise _error("invalid_selection", "Selection references an unknown element")
        return elements[element_id]
    raise _error("invalid_selection", "Selection region kind is unsupported")


def _normalize_source(raw: Mapping[str, Any], source: TrustedSource, *, store, task_id: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise _error("invalid_selection", "Selection source must be an object")
    if source.original_ref.task_id != task_id:
        raise _error("artifact_scope", "Selection source is outside the current task")
    if source.canonical_ref is not None and (
        source.canonical_ref.task_id != task_id or source.canonical_ref.role != "canonical"
    ):
        raise _error("artifact_scope", "Trusted canonical is outside the current task")
    allowed = {"source_id", "original_sha256", "layout_ref", "target_regions", "keep_elements", "remove_elements"}
    if set(raw) - allowed:
        raise _error("invalid_selection", "Selection source contains unsupported fields")
    source_id = raw.get("source_id", source.source_id)
    if source_id != source.source_id:
        raise _error("source_scope", "Selection references an unapproved source")
    supplied_hash = raw.get("original_sha256")
    if supplied_hash is not None and supplied_hash != source.original_sha256:
        raise _error("selection_conflict", "Selection source hash differs from the trusted image")
    # The trusted hash is an integrity binding, not merely metadata.  Recheck
    # it even for bbox-only selections whose dimensions were supplied by the
    # frozen intake manifest.
    store.read(source.original_ref)
    width, height = _dimensions(store, source)
    if _normalized_pixels(store, source.original_ref)[0] != (width, height):
        raise _error("selection_conflict", "Source dimensions differ from the frozen dimensions")
    if source.canonical_ref is not None:
        if _normalized_pixels(store, source.original_ref) != _normalized_pixels(store, source.canonical_ref):
            raise _error("selection_conflict", "Canonical pixels do not match the trusted original")
    layout_ref = source.layout_ref
    supplied_layout = raw.get("layout_ref")
    if supplied_layout is not None:
        try:
            candidate = ArtifactRef.model_validate(supplied_layout)
        except Exception as exc:
            raise _error("artifact_scope", "Selection layout reference is invalid") from exc
        if layout_ref is None:
            raise _error("artifact_scope", "Selection layout is not a trusted layout")
        if candidate != layout_ref:
            raise _error("selection_conflict", "Selection layout differs from the frozen layout")
        layout_ref = candidate
    target_regions = raw.get("target_regions")
    if not isinstance(target_regions, list) or not target_regions:
        raise _error("invalid_selection", "Selection needs at least one target region")
    keep = raw.get("keep_elements", [])
    remove = raw.get("remove_elements", [])
    if not isinstance(keep, list) or not isinstance(remove, list):
        raise _error("invalid_selection", "Selection element lists are invalid")
    if len(set(keep)) != len(keep) or len(set(remove)) != len(remove) or set(keep) & set(remove):
        raise _error("invalid_selection", "Selection keep/remove elements must be unique and disjoint")
    requires_layout = (
        any(isinstance(item, Mapping) and item.get("kind") == "element" for item in target_regions)
        or bool(keep or remove)
    )
    layout = None
    if requires_layout or layout_ref is not None:
        if layout_ref is None:
            raise _error("invalid_selection", "Element selections require a frozen layout reference")
        layout = _load_layout(store, layout_ref, task_id=task_id, source=source)
    elements = _layout_elements(layout or {})
    for element_id in [*keep, *remove]:
        if not isinstance(element_id, str) or element_id not in elements:
            raise _error("invalid_selection", "Selection references an unknown element")
    regions = []
    region_boxes = []
    for region in target_regions:
        if not isinstance(region, Mapping):
            raise _error("invalid_selection", "Selection region is invalid")
        kind = region.get("kind")
        expected_keys = {"kind", "xyxy"} if kind == "bbox" else {"kind", "element_id"}
        if kind not in {"bbox", "element"} or set(region) != expected_keys:
            raise _error("invalid_selection", "Selection region contains unsupported fields")
        box = _region_box(region, elements)
        if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
            raise _error("invalid_selection", "Selection bbox is outside the canonical image")
        if region["kind"] == "bbox":
            normalized = {"kind": "bbox", "xyxy": list(region["xyxy"])}
        else:
            normalized = {"kind": "element", "element_id": region["element_id"]}
        regions.append(normalized)
        region_boxes.append(box)
    for element_id in remove:
        if not _covered_by_union(elements[element_id], region_boxes):
            raise _error("invalid_selection", "Removed elements must be inside the selected region")
    result = {
        "source_id": source.source_id,
        "original_sha256": source.original_sha256,
        "target_regions": regions,
        "keep_elements": list(keep),
        "remove_elements": list(remove),
    }
    if layout_ref is not None:
        if layout_ref.task_id != task_id or layout_ref.role not in {"layout", "review_layout"}:
            raise _error("artifact_scope", "Selection layout is outside the current task")
        store.read(layout_ref)
        result["layout_ref"] = layout_ref.model_dump(mode="json")
    return result


def validate_selection(
    store,
    task_id: str,
    value: Mapping[str, Any] | UISelection,
    sources: Mapping[str, TrustedSource | ArtifactRef | Mapping[str, Any]] | Iterable[TrustedSource | ArtifactRef],
) -> UISelection:
    """Validate geometry, IDs, source scope and trusted refs deterministically."""

    source_map = _source_map(sources)
    raw = value.model_dump(mode="json") if isinstance(value, UISelection) else value
    if not isinstance(raw, Mapping) or set(raw) - {"schema_version", "sources"} or raw.get("schema_version", 1) != 1:
        raise _error("invalid_selection", "Unsupported UI selection schema")
    raw_sources = raw.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources or len(raw_sources) > 10:
        raise _error("invalid_selection", "Selection source count is outside the approved range")
    by_id = {item.get("source_id"): item for item in raw_sources if isinstance(item, Mapping)}
    if len(by_id) != len(raw_sources):
        raise _error("invalid_selection", "Selection source IDs must be unique")
    if set(by_id) != set(source_map):
        raise _error("source_scope", "Selection must cover exactly the approved sources")
    normalized = {
        "schema_version": 1,
        "sources": [
            _normalize_source(by_id[source.source_id], source, store=store, task_id=task_id)
            for source in source_map.values()
        ],
    }
    try:
        return UISelection.model_validate(normalized)
    except Exception as exc:
        raise _error("invalid_selection", "Selection does not match the UI selection contract") from exc


def register_selection(
    store,
    task_id: str,
    value: Mapping[str, Any] | UISelection,
    sources,
    *,
    operation_id: str = "selection",
) -> SelectionPreparation:
    trusted = _source_map(sources)
    parsed = validate_selection(store, task_id, value, trusted)
    refs = [source.original_ref for source in trusted.values()]
    ref = store.put(
        task_id,
        operation_id,
        canonical_json(parsed).encode(),
        role="selection",
        source_ids=[item.artifact_id for item in refs],
    )
    return SelectionPreparation(parsed, ref)


def _candidate_number(index: int) -> str:
    if type(index) is not int or not 1 <= index <= 64:
        raise ValueError("candidate index must be between 1 and 64")
    return f"candidate-{index}"


def _preview_png(store, selection: UISelection, sources) -> bytes:
    """Render a small, deterministic overlay for human range inspection."""
    from io import BytesIO

    source_map = _source_map(sources)
    images = []
    for source_item in selection.sources:
        source = source_map[source_item.source_id]
        size, pixels = _normalized_pixels(store, source.canonical_ref or source.original_ref)
        image = Image.frombytes("RGBA", size, pixels).convert("RGB")
        elements = {}
        if source_item.layout_ref is not None:
            elements = _layout_elements(
                _load_layout(
                    store,
                    source_item.layout_ref,
                    task_id=source.original_ref.task_id,
                    source=source,
                )
            )
        drawing = ImageDraw.Draw(image)
        for region in source_item.target_regions:
            box = _region_box(region.model_dump(mode="json"), elements)
            drawing.rectangle(box, outline=(255, 196, 0), width=max(1, min(image.size) // 256))
        for element_id in source_item.keep_elements:
            drawing.rectangle(elements[element_id], outline=(48, 220, 144), width=max(1, min(image.size) // 256))
        for element_id in source_item.remove_elements:
            drawing.rectangle(elements[element_id], outline=(240, 80, 80), width=max(1, min(image.size) // 256))
        images.append(image)
    if not images:
        raise _error("invalid_selection", "Selection has no preview image")
    output = BytesIO()
    images[0].save(output, format="PNG", optimize=False)
    return output.getvalue()


def _put_catalog(store, task_id: str, catalog: SelectionCatalog) -> ArtifactRef:
    payload = {
        "schema_version": 1,
        "task_id": task_id,
        "revision": catalog.revision,
        "state": catalog.state,
        "candidates": [item.model_dump(mode="json") for item in catalog.candidates],
        "selection_ref": catalog.selection_ref.model_dump(mode="json") if catalog.selection_ref else None,
        "model_calls": catalog.model_calls,
    }
    return store.put(
        task_id,
        f"selection-index-{catalog.revision}",
        canonical_json(payload).encode(),
        role="selection_index",
        source_ids=[item.selection_ref.artifact_id for item in catalog.candidates]
        or ([catalog.selection_ref.artifact_id] if catalog.selection_ref else []),
    )


def _source_bindings(trusted: Mapping[str, TrustedSource]) -> list[dict[str, Any]]:
    bindings = []
    for source in trusted.values():
        item: dict[str, Any] = {
            "source_id": source.source_id,
            "original_ref": source.original_ref.model_dump(mode="json"),
        }
        if source.canonical_ref is not None:
            item["canonical_ref"] = source.canonical_ref.model_dump(mode="json")
        if source.layout_ref is not None:
            item["layout_ref"] = source.layout_ref.model_dump(mode="json")
        bindings.append(item)
    return bindings


def _verify_candidate_artifacts(store, task_id: str, candidate: UISelectionCandidate) -> None:
    """Revalidate a persisted proposal against its original trusted inputs."""
    evidence_raw = store.read(candidate.evidence_ref)
    try:
        evidence = json.loads(evidence_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error("artifact_changed", "Candidate evidence is not valid JSON") from exc
    candidate_data = evidence.get("candidate") if isinstance(evidence, dict) else None
    if not isinstance(candidate_data, dict):
        raise _error("artifact_changed", "Candidate evidence is incomplete")
    for field in ("candidate_id", "task_id", "selection_ref", "preview_ref", "origin", "revision"):
        expected = getattr(candidate, field)
        if field.endswith("_ref"):
            expected = expected.model_dump(mode="json")
        if candidate_data.get(field) != expected:
            raise _error("artifact_changed", "Candidate evidence does not match its index")
    try:
        selection_ref = ArtifactRef.model_validate(candidate_data["selection_ref"])
        preview_ref = ArtifactRef.model_validate(candidate_data["preview_ref"])
    except Exception as exc:
        raise _error("artifact_changed", "Candidate evidence references are invalid") from exc
    if selection_ref != candidate.selection_ref or preview_ref != candidate.preview_ref:
        raise _error("artifact_changed", "Candidate evidence references changed")
    if evidence.get("selection_hash") != candidate.selection_ref.sha256:
        raise _error("artifact_changed", "Candidate selection hash is invalid")
    source_bindings = evidence.get("source_bindings")
    if not isinstance(source_bindings, list) or not source_bindings:
        raise _error("artifact_changed", "Candidate trusted source bindings are missing")
    trusted: dict[str, TrustedSource] = {}
    for item in source_bindings:
        if not isinstance(item, Mapping) or not isinstance(item.get("source_id"), str):
            raise _error("artifact_changed", "Candidate trusted source binding is invalid")
        try:
            original = ArtifactRef.model_validate(item.get("original_ref"))
            layout = ArtifactRef.model_validate(item["layout_ref"]) if item.get("layout_ref") else None
            canonical = ArtifactRef.model_validate(item["canonical_ref"]) if item.get("canonical_ref") else None
        except Exception as exc:
            raise _error("artifact_changed", "Candidate trusted source reference is invalid") from exc
        if original.task_id != task_id or original.role not in {"original", "canonical"}:
            raise _error("artifact_scope", "Candidate original source is outside the current task")
        if layout is not None and (layout.task_id != task_id or layout.role not in {"layout", "review_layout"}):
            raise _error("artifact_scope", "Candidate layout is outside the current task")
        if canonical is not None and (canonical.task_id != task_id or canonical.role != "canonical"):
            raise _error("artifact_scope", "Candidate canonical is outside the current task")
        if item["source_id"] in trusted:
            raise _error("artifact_changed", "Candidate trusted source bindings are duplicated")
        trusted[item["source_id"]] = TrustedSource(
            item["source_id"], original, layout_ref=layout, canonical_ref=canonical
        )
    try:
        value = json.loads(store.read(candidate.selection_ref))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error("artifact_changed", "Candidate selection is not valid JSON") from exc
    parsed = validate_selection(store, task_id, value, trusted)
    if evidence.get("selection") != parsed.model_dump(mode="json"):
        raise _error("artifact_changed", "Candidate evidence selection changed")
    if candidate.selection_ref.sha256 != hashlib.sha256(canonical_json(parsed).encode()).hexdigest():
        raise _error("artifact_changed", "Candidate selection content changed")
    store.read(candidate.preview_ref)


def _catalog_refs(store, task_id: str) -> list[tuple[ArtifactRef, SelectionCatalog]]:
    root = store.root / task_id
    if not root.is_dir():
        return []
    result = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.parent.name != "selection_index":
            continue
        try:
            payload = json.loads(path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _error("artifact_changed", "Selection index is not valid JSON") from exc
        if not isinstance(payload, dict) or payload.get("task_id") != task_id:
            raise _error("artifact_scope", "Selection index belongs to another task")
        if type(payload.get("revision")) is not int or payload["revision"] < 0:
            raise _error("artifact_changed", "Selection index revision is invalid")
        raw_candidates = payload.get("candidates", [])
        if not isinstance(raw_candidates, list):
            raise _error("artifact_changed", "Selection index candidates are invalid")
        try:
            candidates = tuple(UISelectionCandidate.model_validate(item) for item in raw_candidates)
            selected = payload.get("selection_ref")
            selection_ref = ArtifactRef.model_validate(selected) if selected else None
            ref = _ref_for_file(store, task_id, path, "selection_index", payload)
        except PipelineError:
            raise
        except Exception as exc:
            raise _error("artifact_changed", "Selection index contains invalid references") from exc
        candidate_ids = [candidate.candidate_id for candidate in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise _error("selection_conflict", "Selection index has duplicate candidate IDs")
        if selection_ref is not None:
            if selection_ref.task_id != task_id or selection_ref.role != "selection":
                raise _error("artifact_scope", "Selection index output is outside the task")
            store.read(selection_ref)
        for candidate in candidates:
            if candidate.task_id != task_id:
                raise _error("artifact_scope", "Selection candidate is outside the task")
            for candidate_ref in (candidate.selection_ref, candidate.preview_ref, candidate.evidence_ref):
                store.read(candidate_ref)
            _verify_candidate_artifacts(store, task_id, candidate)
        result.append(
            (
                ref,
                SelectionCatalog(
                    task_id,
                    payload["revision"],
                    payload.get("state", "awaiting_selection"),
                    candidates,
                    selection_ref,
                    payload.get("model_calls", 0),
                ),
            )
        )
    return result


def selection_catalog(store, task_id: str) -> SelectionCatalog | None:
    """Read the newest immutable selection index for inspect/select."""
    values = _catalog_refs(store, task_id)
    if not values:
        return None
    revision = max(item[1].revision for item in values)
    latest = [item[1] for item in values if item[1].revision == revision]
    if len(latest) != 1:
        raise _error("selection_conflict", "Selection index has competing current revisions")
    return latest[0]


def record_selection(
    store,
    task_id: str,
    selection_ref: ArtifactRef,
    *,
    revision: int = 0,
    state: str = "awaiting_approval",
) -> SelectionCatalog:
    """Record a trusted selection binding as an immutable catalog revision."""
    if selection_ref.task_id != task_id or selection_ref.role != "selection":
        raise _error("artifact_scope", "Selection output is outside the current task")
    store.read(selection_ref)
    current = selection_catalog(store, task_id)
    next_revision = max(revision, current.revision + 1 if current else revision)
    catalog = SelectionCatalog(task_id, next_revision, state, selection_ref=selection_ref)
    _put_catalog(store, task_id, catalog)
    return catalog


def propose_candidates(
    store,
    task_id: str,
    values,
    sources,
    *,
    revision: int = 0,
    operation_id: str = "selection",
) -> list[CandidatePreparation]:
    """Persist one or more deterministic proposals and their current index."""
    prepared = [
        create_candidate(
            store,
            task_id,
            value,
            sources,
            index=index,
            revision=revision,
            operation_id=operation_id,
            _record=False,
        )
        for index, value in enumerate(values, 1)
    ]
    if prepared:
        state = "awaiting_approval" if len(prepared) == 1 else "awaiting_selection"
        _put_catalog(
            store,
            task_id,
            SelectionCatalog(task_id, revision, state, tuple(item.candidate for item in prepared)),
        )
    return prepared


def create_candidate(
    store,
    task_id: str,
    value: Mapping[str, Any] | UISelection,
    sources,
    *,
    index: int = 1,
    revision: int = 0,
    origin: str = "agent_proposed",
    operation_id: str = "selection",
    _record: bool = True,
) -> CandidatePreparation:
    """Persist a deterministic candidate, preview and evidence, without calls."""

    trusted = _source_map(sources)
    prepared = register_selection(store, task_id, value, trusted, operation_id=operation_id)
    candidate_id = _candidate_number(index)
    preview_bytes = _preview_png(store, prepared.selection, trusted)
    preview_ref = store.put(
        task_id,
        operation_id,
        preview_bytes,
        role="selection_preview",
        media_type="image/png",
        source_ids=[prepared.selection_ref.artifact_id],
    )
    candidate_data = {
        "candidate_id": candidate_id,
        "task_id": task_id,
        "selection_ref": prepared.selection_ref.model_dump(mode="json"),
        "preview_ref": preview_ref.model_dump(mode="json"),
        "origin": origin,
        "revision": revision,
        "state": "proposed",
    }
    evidence_payload = {
        "schema_version": 1,
        "candidate": candidate_data,
        "selection": prepared.selection.model_dump(mode="json"),
        "preview": {"media_type": "image/png", "purpose": "selected-region-overlay"},
        "source_bindings": _source_bindings(trusted),
        "model_calls": 0,
        "validation": "trusted_local",
        "selection_hash": prepared.selection_ref.sha256,
    }
    evidence_ref = store.put(
        task_id,
        operation_id,
        canonical_json(evidence_payload).encode(),
        role="selection_evidence",
        source_ids=[prepared.selection_ref.artifact_id, preview_ref.artifact_id],
    )
    candidate = UISelectionCandidate(
        **candidate_data,
        evidence_ref=evidence_ref,
    )
    result = CandidatePreparation(candidate, prepared.selection)
    if _record:
        _put_catalog(
            store,
            task_id,
            SelectionCatalog(task_id, revision, "awaiting_approval", (candidate,), model_calls=0),
        )
    return result


def find_candidate(store, task_id: str, candidate_id: str) -> UISelectionCandidate:
    """Find a candidate by its trusted evidence artifact, verifying every hash."""

    if not isinstance(candidate_id, str) or not candidate_id.startswith("candidate-"):
        raise _error("invalid_selection", "Candidate ID is invalid")
    catalog = selection_catalog(store, task_id)
    if catalog is not None:
        matches = [item for item in catalog.candidates if item.candidate_id == candidate_id]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise _error("selection_conflict", "Candidate ID is ambiguous in the current selection")
        raise _error("invalid_selection", "Candidate is not present in the current selection")
    raise _error("invalid_selection", "Candidate is unavailable")


def _ref_for_file(store, task_id: str, path: Path, role: str, payload: Mapping[str, Any]) -> ArtifactRef:
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if path.name != sha:
        raise _error("artifact_changed", "Selection evidence hash is invalid")
    # Artifact IDs are content/role derived by ArtifactStore.  The operation
    # is the path component immediately before the role directory.
    try:
        operation_id = path.parent.parent.name
        value = ArtifactRef(
            task_id=task_id,
            artifact_id=f"asset-{hashlib.sha256((sha + role).encode()).hexdigest()[:48]}",
            key=path.relative_to(store.root).as_posix(),
            sha256=sha,
            size_bytes=len(data),
            media_type="application/json",
            role=role,
            operation_id=operation_id,
        )
    except Exception as exc:
        raise _error("artifact_changed", "Selection evidence reference is invalid") from exc
    store.read(value)
    return value


def select_candidate(store, task_id: str, candidate_id: str) -> SelectionPreparation:
    candidate = find_candidate(store, task_id, candidate_id)
    if candidate.task_id != task_id or candidate.selection_ref.task_id != task_id:
        raise _error("artifact_scope", "Candidate is outside the current task")
    catalog = selection_catalog(store, task_id)
    if candidate.state == "selected":
        if catalog is None or catalog.selection_ref is None:
            raise _error("selection_conflict", "Selected candidate has no frozen selection")
        chosen_ref = catalog.selection_ref
        try:
            value = json.loads(store.read(chosen_ref))
            parsed = UISelection.model_validate(value)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise _error("artifact_changed", "Frozen selection is invalid") from exc
        return SelectionPreparation(parsed, chosen_ref)
    if candidate.state != "proposed":
        raise _error("invalid_selection", "Candidate is no longer selectable")
    value = json.loads(store.read(candidate.selection_ref))
    try:
        UISelection.model_validate(value)
    except Exception as exc:
        raise _error("invalid_selection", "Candidate selection is invalid") from exc
    # find_candidate revalidates source bytes and the frozen layout locally.
    ref = store.put(
        task_id,
        "selection-select",
        canonical_json(value).encode(),
        role="selection",
        source_ids=[candidate.selection_ref.artifact_id],
    )
    _put_catalog(
        store,
        task_id,
        SelectionCatalog(
            task_id,
            (catalog.revision + 1 if catalog else candidate.revision + 1),
            "awaiting_approval",
            tuple(
                item.model_copy(update={"state": "selected" if item.candidate_id == candidate_id else "proposed"})
                for item in catalog.candidates
            ),
            ref,
            0,
        ),
    )
    return SelectionPreparation(UISelection.model_validate(value), ref)


def reviewed_binding(service, reviewed_task: str, review_revision: int | None = None):
    """Read one confirmed revision, resolving an omitted revision once."""
    from .review import ReviewRepository

    repository = ReviewRepository(service.ledger, service.artifacts)
    if review_revision is None:
        head = repository.head(reviewed_task)
        review_revision = head.get("confirmed_revision")
        if type(review_revision) is not int or review_revision < 0:
            raise _error("review_not_confirmed", "The reviewed task has no confirmed revision")
    if type(review_revision) is not int or review_revision < 0:
        raise _error("invalid_selection", "Review revision must be a non-negative integer")
    return repository.binding(reviewed_task, review_revision)


def _is_ref_dict(value: Any) -> bool:
    return isinstance(value, Mapping) and {"task_id", "artifact_id", "sha256", "role"} <= set(value)


def _collect_refs(value: Any, result: dict[tuple[str, str, str], ArtifactRef]) -> None:
    if isinstance(value, Mapping):
        if _is_ref_dict(value):
            try:
                ref = ArtifactRef.model_validate(value)
                result[(ref.task_id, ref.sha256, ref.role)] = ref
                return
            except Exception:
                pass
        for child in value.values():
            _collect_refs(child, result)
    elif isinstance(value, list):
        for child in value:
            _collect_refs(child, result)


def _rewrite_refs(value: Any, mapping: Mapping[tuple[str, str, str], ArtifactRef]) -> Any:
    if isinstance(value, Mapping):
        if _is_ref_dict(value):
            try:
                ref = ArtifactRef.model_validate(value)
                replacement = mapping.get((ref.task_id, ref.sha256, ref.role))
                if replacement is not None:
                    return replacement.model_dump(mode="json")
            except Exception:
                pass
        return {key: _rewrite_refs(child, mapping) for key, child in value.items()}
    if isinstance(value, list):
        return [_rewrite_refs(child, mapping) for child in value]
    return value


def rebind_reviewed_layout(service, binding, task_id: str) -> tuple[dict[str, Any], dict[str, ArtifactRef]]:
    """Copy one confirmed binding into a new task scope.

    Rebinding is content-addressed and explicit.  The original confirmed
    artifacts remain immutable in their source task; the returned refs are the
    only refs a new plan may carry.
    """

    if binding.task_id == task_id:
        return binding.model_dump(mode="json"), {
            "canonical_ref": binding.canonical_ref,
            "layout_ref": binding.layout_ref,
            "texts_ref": binding.texts_ref,
            "review_manifest_ref": binding.review_manifest_ref,
        }
    store = service.artifacts
    manifest = json.loads(store.read(binding.review_manifest_ref))
    from ..schemas.ui_review import ReviewDocument

    try:
        original_layout = ReviewDocument.model_validate_json(store.read(binding.layout_ref))
    except Exception as exc:
        raise _error("review_schema_unsupported", "Confirmed review layout is invalid") from exc
    if original_layout.task_id != binding.task_id:
        raise _error("artifact_scope", "Confirmed review layout task scope is invalid")
    # ReviewDocument is deliberately separate from layout v1.  Rebinding only
    # changes its owning task; canonical/source provenance stays in the copied
    # manifest and base refs, never as extra DTO fields.
    layout = original_layout.model_copy(update={"task_id": task_id}).model_dump(mode="json")
    texts = json.loads(store.read(binding.texts_ref))
    if not isinstance(texts, dict) or texts.get("task_id") != binding.task_id:
        raise _error("artifact_scope", "Confirmed review texts task scope is invalid")
    texts = {**texts, "task_id": task_id}
    source_refs: dict[tuple[str, str, str], ArtifactRef] = {}
    _collect_refs(manifest, source_refs)
    _collect_refs(layout, source_refs)
    _collect_refs(texts, source_refs)
    for ref in (binding.canonical_ref, binding.layout_ref, binding.texts_ref, binding.review_manifest_ref):
        source_refs[(ref.task_id, ref.sha256, ref.role)] = ref
    mapping: dict[tuple[str, str, str], ArtifactRef] = {}
    canonical_ref = store.put(
        task_id,
        "review-input",
        store.read(binding.canonical_ref),
        role="canonical",
        media_type=binding.canonical_ref.media_type,
        source_ids=[binding.canonical_ref.artifact_id],
    )
    mapping[(binding.canonical_ref.task_id, binding.canonical_ref.sha256, binding.canonical_ref.role)] = canonical_ref
    # Copy every referenced review artifact into the new task scope before
    # rewriting the manifest.  No old task ref may survive this operation.
    for key, ref in source_refs.items():
        if key in mapping or ref == binding.review_manifest_ref:
            continue
        copied = store.put(
            task_id,
            "review-input",
            store.read(ref),
            role=ref.role,
            media_type=ref.media_type,
            source_ids=[canonical_ref.artifact_id],
        )
        mapping[key] = copied
    layout = _rewrite_refs(layout, mapping)
    layout_ref = store.put(
        task_id,
        "review-input",
        canonical_json(layout).encode(),
        role="review_layout",
        source_ids=[canonical_ref.artifact_id],
    )
    texts = _rewrite_refs(texts, mapping)
    texts_ref = store.put(
        task_id,
        "review-input",
        canonical_json(texts).encode(),
        role="review_texts",
        source_ids=[canonical_ref.artifact_id],
    )
    mapping[(binding.layout_ref.task_id, binding.layout_ref.sha256, binding.layout_ref.role)] = layout_ref
    mapping[(binding.texts_ref.task_id, binding.texts_ref.sha256, binding.texts_ref.role)] = texts_ref
    manifest = _rewrite_refs(manifest, mapping)
    manifest["task_id"] = task_id
    manifest["review_revision"] = binding.review_revision
    manifest["rebound_from"] = {
        "review_revision": binding.review_revision,
        "canonical_sha256": binding.canonical_ref.sha256,
        "manifest_sha256": binding.review_manifest_ref.sha256,
    }
    manifest.setdefault("base_refs", {})["canonical_ref"] = canonical_ref.model_dump(mode="json")
    manifest.setdefault("outputs", {}).update(
        layout_ref=layout_ref.model_dump(mode="json"),
        texts_ref=texts_ref.model_dump(mode="json"),
    )
    manifest_ref = store.put(
        task_id,
        "review-input",
        canonical_json(manifest).encode(),
        role="review_manifest",
        source_ids=[layout_ref.artifact_id, texts_ref.artifact_id],
    )
    refs = {
        "canonical_ref": canonical_ref,
        "layout_ref": layout_ref,
        "texts_ref": texts_ref,
        "review_manifest_ref": manifest_ref,
    }
    return {
        "task_id": task_id,
        "review_revision": binding.review_revision,
        "review_manifest_ref": manifest_ref.model_dump(mode="json"),
        "layout_ref": layout_ref.model_dump(mode="json"),
        "texts_ref": texts_ref.model_dump(mode="json"),
        "canonical_ref": canonical_ref.model_dump(mode="json"),
        "evaluation_lane": "human_assisted",
    }, refs


__all__ = [
    "CandidatePreparation",
    "MAX_SELECTION_BYTES",
    "SelectionCatalog",
    "SelectionPreparation",
    "TrustedSource",
    "create_candidate",
    "find_candidate",
    "load_document",
    "propose_candidates",
    "record_selection",
    "rebind_reviewed_layout",
    "register_selection",
    "reviewed_binding",
    "select_candidate",
    "selection_catalog",
    "source_id_for_ref",
    "validate_selection",
]
