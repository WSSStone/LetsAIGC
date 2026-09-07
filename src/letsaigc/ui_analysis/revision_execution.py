"""Provider-free execution planning for bounded UI revisions.

This coordinator only copies immutable inputs, registers exact child plans and
projects completed child artifacts.  OCR, VLM, SAM and Comfy execution remain
behind the existing child approval and activity boundaries.
"""

from __future__ import annotations

import io
import json
import math
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any, Literal

from PIL import Image
from pydantic import Field

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, PipelineModel, PipelinePlan, canonical_json, digest
from ..schemas.ui import ImageView, UIAnalysisRequest, UISelection, UISelectionSource
from ..schemas.ui_review import ReviewedLayoutBinding
from ..ui_analysis.revision import (
    RevisionAction,
    RevisionArtifact,
    RevisionNode,
    RevisionProjection,
    RevisionRequest,
    project_revision,
)
from .editing import (
    EditingExecution,
    EditingPreparation,
    _child_operation_state,
    _copy_nested_artifact,
    _ref,
    _release_confirmed,
    _selection_ref,
    _selection_revision,
)
from .revision_inputs import LocalRevisionInputs

RevisionState = Literal[
    "awaiting_selection",
    "awaiting_approval",
    "awaiting_reconciliation",
    "succeeded",
    "failed",
]


class RevisionPreparation(PipelineModel):
    """Immutable revision planning result returned before child execution."""

    root_task_id: str
    revision_ref: ArtifactRef
    state: RevisionState
    child: PipelinePlan | None = None
    impact: RevisionProjection
    artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=128)
    reason: str | None = None
    selection_ref: ArtifactRef | None = None
    selection_revision: int | None = Field(default=None, ge=0, strict=True)
    source_id: str | None = None


class _RevisionPlanRecord(PipelineModel):
    schema_version: Literal[1] = 1
    kind: Literal["ui_revision_plan"] = "ui_revision_plan"
    root_task_id: str
    child_task_id: str
    child_fingerprint: str
    request_ref: ArtifactRef
    request: RevisionRequest
    base_plan: PipelinePlan
    base_outputs: list[ArtifactRef] = Field(default_factory=list, max_length=128)
    child: PipelinePlan
    impact: RevisionProjection
    selection_ref: ArtifactRef
    selection_revision: int = Field(ge=0, strict=True)
    canonical_ref: ArtifactRef
    layout_ref: ArtifactRef | None = None
    texts_ref: ArtifactRef | None = None
    source_id: str


def _error(code: str, message: str = "Revision cannot be prepared") -> PipelineError:
    return PipelineError(code, message)


def _ref_role(value: Any, role: str) -> ArtifactRef:
    try:
        ref = value if isinstance(value, ArtifactRef) else ArtifactRef.model_validate(value)
    except Exception as exc:
        raise _error("artifact_scope") from exc
    if ref.role != role:
        raise _error("artifact_scope")
    return ref


def _json(store, ref: ArtifactRef, label: str) -> Any:
    try:
        return json.loads(store.read(ref))
    except PipelineError:
        raise
    except Exception as exc:
        raise _error("artifact_changed", label) from exc


def _plan(service, task_id: str, fingerprint: str) -> PipelinePlan:
    try:
        return service.checked_plan(task_id, fingerprint)
    except PipelineError:
        raise
    except Exception as exc:
        raise _error("plan_changed") from exc


def _outputs(service, task_id: str) -> list[ArtifactRef]:
    result: dict[tuple[str, str], ArtifactRef] = {}
    for operation in service.ledger.list_operations(task_id):
        if operation.state != "succeeded":
            continue
        for value in operation.result.get("artifacts", []):
            ref = _ref(value)
            service.artifacts.read(ref)
            result[(ref.role, ref.sha256)] = ref
    return list(result.values())


def _all_refs(request: UIAnalysisRequest) -> list[ArtifactRef]:
    return request.references()


def _copy_ref(service, ref: ArtifactRef, task_id: str, operation: str, *, role: str | None = None) -> ArtifactRef:
    service.artifacts.read(ref)
    return service.artifacts.put(
        task_id,
        operation,
        service.artifacts.read(ref),
        role=role or ref.role,
        media_type=ref.media_type,
        source_ids=[ref.artifact_id],
    )


def _copy_context_ref(
    service,
    ref: ArtifactRef,
    task_id: str,
    operation: str,
    mapping: dict[tuple[str, str, str], ArtifactRef],
) -> ArtifactRef:
    """Copy structured context closures while allowing opaque model bytes."""
    if ref.role in {"vlm_policy", "input_manifest", "review_manifest", "provenance"}:
        return _copy_nested_artifact(service, ref, task_id, operation, mapping)
    return _copy_ref(service, ref, task_id, operation)


def _layout_value(service, ref: ArtifactRef) -> dict[str, Any]:
    value = _json(service.artifacts, ref, "Frozen layout")
    if not isinstance(value, dict) or not isinstance(value.get("elements"), list):
        raise _error("artifact_changed", "Frozen layout has no elements")
    return value


def _texts_value(service, ref: ArtifactRef) -> list[dict[str, Any]]:
    value = _json(service.artifacts, ref, "Frozen OCR texts")
    rows = value.get("texts") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise _error("artifact_changed", "Frozen OCR texts are invalid")
    return [row for row in rows if isinstance(row, dict)]


def _box(value: Any) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4 or any(type(x) is not int for x in value):
        raise _error("invalid_revision", "Revision geometry is invalid")
    x1, y1, x2, y2 = value
    if x2 <= x1 or y2 <= y1:
        raise _error("invalid_revision", "Revision geometry is empty")
    return x1, y1, x2, y2


def _row_id(row: Mapping[str, Any]) -> str | None:
    value = row.get("text_id", row.get("text_region_id"))
    return value if isinstance(value, str) else None


def _row_box(row: Mapping[str, Any]) -> tuple[int, int, int, int]:
    if "bbox" in row:
        return _box(row["bbox"])
    polygon = row.get("polygon")
    if not isinstance(polygon, list) or not polygon:
        raise _error("artifact_changed", "Text geometry is unavailable")
    points: list[tuple[float, float]] = []
    for point in polygon:
        if (
            not isinstance(point, (list, tuple))
            or len(point) != 2
            or any(type(value) not in {int, float} or not math.isfinite(float(value)) for value in point)
        ):
            raise _error("artifact_changed", "Text polygon is invalid")
        points.append((float(point[0]), float(point[1])))
    return (
        math.floor(min(point[0] for point in points)),
        math.floor(min(point[1] for point in points)),
        math.ceil(max(point[0] for point in points)),
        math.ceil(max(point[1] for point in points)),
    )


def _element_boxes(layout: Mapping[str, Any]) -> dict[str, tuple[int, int, int, int]]:
    result: dict[str, tuple[int, int, int, int]] = {}
    for item in layout.get("elements", []):
        if not isinstance(item, Mapping) or not isinstance(item.get("element_id"), str):
            raise _error("artifact_changed", "Layout element is invalid")
        result[item["element_id"]] = _box(item.get("bbox"))
    return result


def _source_contains(source: UISelectionSource, target_id: str, box: tuple[int, int, int, int], elements) -> bool:
    for region in source.target_regions:
        if region.kind == "element":
            if region.element_id == target_id:
                return True
            parent_box = elements.get(region.element_id)
            if (
                parent_box is not None
                and parent_box[0] <= box[0]
                and parent_box[1] <= box[1]
                and box[2] <= parent_box[2]
                and box[3] <= parent_box[3]
            ):
                return True
        if region.kind == "bbox":
            x1, y1, x2, y2 = region.xyxy
            if x1 <= box[0] and y1 <= box[1] and box[2] <= x2 and box[3] <= y2:
                return True
    if target_id in source.keep_elements or target_id in source.remove_elements:
        return True
    return False


def _target_source(selection: UISelection, target_id: str, box: tuple[int, int, int, int], elements):
    matches = [source for source in selection.sources if _source_contains(source, target_id, box, elements)]
    if len(matches) != 1:
        raise _error("source_scope", "Revision target is outside the frozen selection")
    return matches[0]


def _request_ref(plan: PipelinePlan) -> ArtifactRef:
    return _ref_role(plan.parameters.get("request_ref"), "request")


def _root_and_base(service, request: RevisionRequest) -> tuple[PipelinePlan, PipelinePlan]:
    base = _plan(service, request.base_task_id, request.base_fingerprint)
    if base.workflow_type == "ui_analysis":
        return base, base
    if base.workflow_type not in {"ui_segmentation", "ui_inpaint"}:
        raise _error("invalid_plan")
    editing = EditingExecution(service)
    root_id = editing.child_root(base)
    root = service.checked_plan(root_id, service.ledger.plan(root_id).fingerprint)
    if root.workflow_type != "ui_analysis":
        raise _error("invalid_plan")
    return root, base


def _root_request(service, root: PipelinePlan) -> UIAnalysisRequest:
    request = UIAnalysisRequest.model_validate_json(service.artifacts.read(_request_ref(root)))
    if not request.allow_local_revision:
        raise _error("revision_not_allowed")
    if request.output_mode == "parse":
        raise _error("revision_not_allowed", "Local revisions require an editing root")
    if request.selection_ref is not None:
        selection_ref = _ref_role(request.selection_ref, "selection")
        if selection_ref.task_id != root.task_id:
            raise _error("artifact_scope")
        service.artifacts.read(selection_ref)
    return request


def _model_ref(request: UIAnalysisRequest, name: str, role: str) -> ArtifactRef | None:
    value = request.model_bindings.get(name)
    if value is None:
        return None
    return value if value.role == role else None


def _frozen_inputs(service, root: PipelinePlan, request: UIAnalysisRequest, *, require_texts: bool):
    selection_ref, candidates = _selection_ref(service, root, request, None)
    if selection_ref is None:
        raise _error("dependency_not_ready", "Frozen selection is unavailable")
    selection_ref = _ref_role(selection_ref, "selection")
    if selection_ref.task_id != root.task_id:
        raise _error("artifact_scope")
    selection = UISelection.model_validate_json(service.artifacts.read(selection_ref))
    binding = _find_binding(service, request)
    layout_ref = request.model_bindings.get("layout_ref")
    if layout_ref is not None and layout_ref.role not in {"layout", "review_layout"}:
        layout_ref = None
    if layout_ref is None:
        layout_ref = request.model_bindings.get("review_layout_ref")
    if layout_ref is not None and layout_ref.role not in {"layout", "review_layout"}:
        layout_ref = None
    if layout_ref is None:
        layout_ref = next(
            (source.layout_ref for source in selection.sources if source.layout_ref is not None),
            None,
        )
    if layout_ref is None and binding is not None and binding.layout_ref.task_id == root.task_id:
        layout_ref = binding.layout_ref
    if layout_ref is None or layout_ref.task_id != root.task_id:
        raise _error("dependency_not_ready", "Frozen layout is unavailable")
    layout = _layout_value(service, layout_ref)
    canonical_ref = _model_ref(request, "canonical_ref", "canonical")
    if canonical_ref is None and binding is not None:
        canonical_ref = binding.canonical_ref
    if canonical_ref is None:
        canonical_ref = next((ref for ref in _outputs(service, root.task_id) if ref.role == "canonical"), None)
    if canonical_ref is None or canonical_ref.task_id != root.task_id:
        raise _error("dependency_not_ready", "Frozen canonical image is unavailable")
    service.artifacts.read(canonical_ref)
    texts_ref = request.model_bindings.get("texts_ref")
    if texts_ref is not None and texts_ref.role not in {"texts", "review_texts"}:
        texts_ref = None
    if texts_ref is None:
        texts_ref = next((ref for ref in _outputs(service, root.task_id) if ref.role == "texts"), None)
    if texts_ref is None and binding is not None:
        texts_ref = binding.texts_ref
    if texts_ref is None and require_texts:
        raise _error("dependency_not_ready", "Frozen OCR text artifact is unavailable")
    if texts_ref is not None:
        service.artifacts.read(texts_ref)
    return selection_ref, selection, layout_ref, layout, canonical_ref, texts_ref


def _target_geometry(action: RevisionAction, target_ids: Sequence[str], layout, texts):
    elements = _element_boxes(layout)
    text_rows = {_row_id(row): row for row in texts if _row_id(row) is not None}
    if action is RevisionAction.reread_text:
        if len(target_ids) != 1 or target_ids[0] not in text_rows:
            raise _error("unknown_target")
        return {target_ids[0]: _row_box(text_rows[target_ids[0]])}
    missing = [target for target in target_ids if target not in elements]
    if missing:
        raise _error("unknown_target")
    return {target: elements[target] for target in target_ids}


def _artifact_targets(service, ref: ArtifactRef) -> list[str]:
    """Read only stable IDs from structured outputs for impact projection."""
    if ref.role in {"layout", "review_layout"}:
        value = _json(service.artifacts, ref, "Frozen layout")
        return [
            item["element_id"]
            for item in value.get("elements", [])
            if isinstance(item, Mapping) and isinstance(item.get("element_id"), str)
        ]
    if ref.role in {"texts", "review_texts"}:
        return [value for value in (_row_id(item) for item in _texts_value(service, ref)) if value]
    if ref.role == "analysis":
        value = _json(service.artifacts, ref, "Analysis output")
        output = value.get("output", value) if isinstance(value, Mapping) else {}
        result: list[str] = []
        for item in output.get("correction_suggestions", []) if isinstance(output, Mapping) else []:
            if isinstance(item, Mapping) and isinstance(item.get("text_id"), str):
                result.append(item["text_id"])
        for item in output.get("elements", []) if isinstance(output, Mapping) else []:
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                result.append(item["id"])
        return list(dict.fromkeys(result))
    if ref.role == "segmentation":
        value = _json(service.artifacts, ref, "Segmentation output")
        items = value.get("items", []) if isinstance(value, Mapping) else []
        return [
            item["element_id"]
            for item in items
            if isinstance(item, Mapping) and isinstance(item.get("element_id"), str)
        ]
    return []


def _view_box(
    action: RevisionAction, boxes: Mapping[str, tuple[int, int, int, int]], limit: int
) -> tuple[int, int, int, int]:
    values = list(boxes.values())
    x1 = min(value[0] for value in values)
    y1 = min(value[1] for value in values)
    x2 = max(value[2] for value in values)
    y2 = max(value[3] for value in values)
    if x2 - x1 > limit or y2 - y1 > limit:
        raise _error("resource_insufficient")
    return x1, y1, x2, y2


def _identity_view(service, canonical_ref: ArtifactRef, task_id: str, operation: str, box, view_id: str) -> ArtifactRef:
    raw = service.artifacts.read(canonical_ref)
    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
        if box[2] > width or box[3] > height:
            raise _error("artifact_changed")
        crop = image.crop(box)
        stream = io.BytesIO()
        crop.save(stream, format="PNG", optimize=False)
        input_ref = service.artifacts.put(
            task_id,
            operation,
            stream.getvalue(),
            role="view",
            media_type="image/png",
            source_ids=[canonical_ref.artifact_id],
        )
        x1, y1, x2, y2 = box
        view = ImageView(
            view_id=view_id,
            kind="local",
            canonical_ref=canonical_ref,
            input_ref=input_ref,
            crop=box,
            width=x2 - x1,
            height=y2 - y1,
            forward=((1.0, 0.0, -float(x1)), (0.0, 1.0, -float(y1)), (0.0, 0.0, 1.0)),
            inverse=((1.0, 0.0, float(x1)), (0.0, 1.0, float(y1)), (0.0, 0.0, 1.0)),
        )
    return service.artifacts.put(task_id, operation, view.model_dump_json().encode(), role="view_manifest")


def _local_texts(
    service,
    ref: ArtifactRef,
    task_id: str,
    operation: str,
    view_box: tuple[int, int, int, int],
) -> ArtifactRef:
    """Adapt a frozen reviewed text projection to the OCR adapter contract."""
    value = _json(service.artifacts, ref, "Frozen review texts")
    rows = value.get("texts") if isinstance(value, Mapping) else None
    if not isinstance(rows, list):
        raise _error("artifact_changed", "Frozen review texts are invalid")
    normalized = []
    x1, y1, x2, y2 = view_box
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        text_id = _row_id(row)
        text = row.get("text", row.get("effective_text"))
        if not isinstance(text_id, str) or not isinstance(text, str):
            continue
        box = _row_box(row)
        if box[0] >= x2 or x1 >= box[2] or box[1] >= y2 or y1 >= box[3]:
            continue
        # The VLM context uses clipped text bounds in local image pixels.
        # Exact original polygons and human metadata remain in source_ref.
        left, top = max(box[0], x1) - x1, max(box[1], y1) - y1
        right, bottom = min(box[2], x2) - x1, min(box[3], y2) - y1
        normalized.append({
            "text_id": text_id, "text": text,
            "bbox": [left, top, right, bottom],
            "polygon": [[left, top], [right, top], [right, bottom], [left, bottom]],
            "score": row.get("score", row.get("original_score")),
        })
    payload = {
        "schema_version": 1,
        "status": "observed" if normalized else "empty",
        "texts": normalized,
        "source_ref": ref.model_dump(mode="json"),
    }
    return service.artifacts.put(
        task_id,
        operation,
        canonical_json(payload).encode(),
        role="texts",
        source_ids=[ref.artifact_id],
    )


def _local_budget(root: PipelinePlan):
    from ..schemas.agent import TaskBudget

    budget = root.envelope.budget
    return TaskBudget(
        max_total_cost_usd=budget.max_total_cost_usd,
        max_iteration_cost_usd=budget.max_iteration_cost_usd,
        max_total_gpu_minutes=0,
        max_iteration_gpu_minutes=0,
        max_revisions=budget.max_revisions,
    )


def _local_request(
    service,
    root: PipelinePlan,
    request: UIAnalysisRequest,
    revision: RevisionRequest,
    selection_ref: ArtifactRef,
    selection: UISelection,
    source: UISelectionSource,
    canonical_ref: ArtifactRef,
    layout_ref: ArtifactRef,
    texts_ref: ArtifactRef,
    view_box: tuple[int, int, int, int],
    *,
    source_id: str,
    selection_revision: int,
    revision_ref: ArtifactRef,
    child_id: str,
) -> tuple[PipelinePlan, list[ArtifactRef]]:
    from .editing import _artifact_key, _rebind_selection_value

    operation = "input"
    canonical_child = _copy_ref(service, canonical_ref, child_id, operation, role="canonical")
    mapping = {_artifact_key(canonical_ref): canonical_child}
    local_selection = _rebind_selection_value(
        service,
        selection,
        source_id,
        child_id,
        operation,
        reference_mapping=mapping,
    )
    local_selection_value = UISelection.model_validate_json(service.artifacts.read(local_selection))
    local_layout_ref = local_selection_value.sources[0].layout_ref
    if local_layout_ref is None:
        raise _error("dependency_not_ready", "Frozen layout is unavailable")
    copied: dict[tuple[str, str, str], ArtifactRef] = {
        _artifact_key(canonical_ref): canonical_child,
        _artifact_key(layout_ref): local_layout_ref,
    }
    local_inputs: list[ArtifactRef] = []
    for ref in _all_refs(request):
        if ref == request.selection_ref or ref == layout_ref or ref == canonical_ref:
            continue
        key = _artifact_key(ref)
        if key not in copied:
            copied[key] = _copy_context_ref(service, ref, child_id, operation, mapping)
        local_inputs.append(copied[key])
    # The original image is needed by the local request's ManualUIInput even
    # when the root request references it only through a provider manifest.
    input_value = request.input.model_dump(mode="json")
    input_refs = []
    for ref in request.input.inputs if request.input.kind == "manual" else []:
        key = _artifact_key(ref)
        child_ref = copied.get(key) or _copy_ref(service, ref, child_id, operation)
        copied[key] = child_ref
        input_refs.append(child_ref)
    if request.input.kind == "manual":
        input_value["inputs"] = [ref.model_dump(mode="json") for ref in input_refs]
        if request.input.metadata_ref is not None:
            key = _artifact_key(request.input.metadata_ref)
            metadata = copied.get(key) or _copy_ref(service, request.input.metadata_ref, child_id, operation)
            copied[key] = metadata
            input_value["metadata_ref"] = metadata.model_dump(mode="json")
    else:
        for key in ("query_ref", "criteria_ref", "routing_policy_ref"):
            old = getattr(request.input, key)
            child_ref = copied.get(_artifact_key(old)) or _copy_ref(service, old, child_id, operation)
            copied[_artifact_key(old)] = child_ref
            input_value[key] = child_ref.model_dump(mode="json")
    bindings: dict[str, ArtifactRef] = {}
    for name, old in request.model_bindings.items():
        key = _artifact_key(old)
        if old.sha256 == layout_ref.sha256 and old.role in {"layout", "review_layout"}:
            bindings[name] = local_layout_ref
        elif key in copied:
            bindings[name] = copied[key]
        else:
            bindings[name] = _copy_context_ref(service, old, child_id, operation, mapping)
            copied[key] = bindings[name]
    bindings["canonical_ref"] = canonical_child
    bindings["layout_ref"] = local_layout_ref
    local_budget = _local_budget(root)
    local_context = UIAnalysisRequest.model_validate(
        request.model_dump(mode="json")
        | {
            "input": input_value,
            "selection_mode": "bound",
            "selection_ref": local_selection.model_dump(mode="json"),
            "allow_local_revision": True,
            "budget": local_budget.model_dump(mode="json"),
            "model_bindings": {name: ref.model_dump(mode="json") for name, ref in bindings.items()},
        }
    )
    analysis_ref = service.artifacts.put(
        child_id,
        operation,
        local_context.model_dump_json().encode(),
        role="request",
    )
    local_revision_ref = _copy_ref(service, revision_ref, child_id, operation, role="revision_request")
    local_texts = _local_texts(service, texts_ref, child_id, operation, view_box)
    parameters = (
        {"language": local_context.language, "text_id": revision.target_ids[0]}
        if revision.action is RevisionAction.reread_text
        else {"user_notes": revision.parameters.get("user_notes", "")}
    )
    parameters_ref = service.artifacts.put(child_id, operation, canonical_json(parameters).encode(), role="parameters")
    local_view = _identity_view(
        service,
        canonical_child,
        child_id,
        operation,
        view_box,
        "local-" + digest({"action": revision.action, "targets": revision.target_ids})[:32],
    )
    wrapper = LocalRevisionInputs(
        action=revision.action,
        analysis_request_ref=analysis_ref,
        revision_request_ref=local_revision_ref,
        selection_ref=local_selection,
        selection_revision=selection_revision,
        view_ref=local_view,
        texts_ref=local_texts,
        parameters_ref=parameters_ref,
    )
    wrapper_ref = service.artifacts.put(child_id, "request", wrapper.model_dump_json().encode(), role="request")
    child = service.ui_child_plan(
        child_id,
        parent_task_id=root.task_id,
        purpose=revision.action.value,
        source_ids=[source_id],
        request_ref=wrapper_ref,
        selection_ref=selection_ref,
        selection_revision=wrapper.selection_revision,
        budget=local_budget,
    )
    return child, [
        wrapper_ref,
        analysis_ref,
        local_revision_ref,
        local_selection,
        local_view,
        local_texts,
        parameters_ref,
        canonical_child,
        *local_inputs,
    ]


def _adjust_segmentation_child(
    service,
    root: PipelinePlan,
    base: PipelinePlan,
    root_request: UIAnalysisRequest,
    selection: UISelection,
    selection_ref: ArtifactRef,
    source_id: str,
    revision: RevisionRequest,
    selection_revision: int,
    revision_ref: ArtifactRef,
) -> tuple[PipelinePlan, list[ArtifactRef]]:
    """Materialize a prompt-changing SAM child without reusing the old child."""
    from ..schemas.ui import UISegmentationRequest
    from .editing import _artifact_key, _budget, _rebind_selection_value

    base_request = UISegmentationRequest.model_validate_json(service.artifacts.read(_request_ref(base)))
    updates = {item["element_id"]: item for item in revision.parameters["prompts"] if isinstance(item, Mapping)}
    if set(updates) - {prompt.element_id for prompt in base_request.prompts}:
        raise _error("unknown_target", "Revision target has no frozen segmentation prompt")
    base_ids = {prompt.element_id for prompt in base_request.prompts}
    if not set(revision.target_ids) <= base_ids or not set(updates) <= base_ids:
        raise _error("unknown_target")
    prompts = [updates[prompt.element_id] for prompt in base_request.prompts if prompt.element_id in updates]
    child_id = (
        "ui-revision-seg-"
        + digest(
            {
                "root": root.task_id,
                "base": base.fingerprint,
                "revision": revision_ref.sha256,
                "prompts": [
                    value.model_dump(mode="json") if hasattr(value, "model_dump") else value for value in prompts
                ],
            }
        )[:40]
    )
    mapping: dict[tuple[str, str, str], ArtifactRef] = {}
    canonical_child = _copy_ref(
        service,
        base_request.canonical_ref,
        child_id,
        "input",
        role="canonical",
    )
    mapping[_artifact_key(base_request.canonical_ref)] = canonical_child
    local_selection = _rebind_selection_value(
        service,
        selection,
        source_id,
        child_id,
        "input",
        reference_mapping=mapping,
    )
    snapshot_child = _copy_ref(
        service,
        base_request.model_snapshot_ref,
        child_id,
        "model",
        role="model_snapshot",
    )
    child_request = UISegmentationRequest(
        canonical_ref=canonical_child,
        selection_ref=local_selection,
        selection_revision=selection_revision,
        selection_hash=local_selection.sha256,
        prompts=prompts,
        model_snapshot_ref=snapshot_child,
        prompt_version="revision-v1",
        resources=base_request.resources,
        result_roles=base_request.result_roles,
    )
    from ..vision.segmentation import _canonical_image, _selection_geometry, _validate_prompts

    image = _canonical_image(service.artifacts, canonical_child)
    try:
        geometry = _selection_geometry(service.artifacts, canonical_child, local_selection, image)
        _validate_prompts(SimpleNamespace(
            prompts=child_request.prompts,
            parameters_hash=digest({"prompt_version": child_request.prompt_version,
                                    "prompts": [item.model_dump(mode="json") for item in child_request.prompts]}),
        ), image, geometry)
    finally:
        image.close()
    request_ref = service.artifacts.put(
        child_id,
        "request",
        canonical_json(child_request).encode(),
        role="request",
    )
    child_budget = _budget(root_request.budget, root.envelope.budget)
    child = service.ui_child_plan(
        child_id,
        parent_task_id=base.task_id,
        purpose="segmentation",
        source_ids=[source_id],
        request_ref=request_ref,
        selection_ref=selection_ref,
        selection_revision=selection_revision,
        budget=child_budget,
    )
    return child, [local_selection, canonical_child, snapshot_child, request_ref]


def _analysis_artifact(service, child: PipelinePlan) -> ArtifactRef | None:
    return next((ref for ref in _outputs(service, child.task_id) if ref.role in {"texts", "analysis"}), None)


def _suggestion_payload(
    service,
    revision: RevisionRequest,
    child: PipelinePlan,
    artifacts: Sequence[ArtifactRef],
    *,
    target_text_ids: set[str] | None = None,
    target_box: tuple[int, int, int, int] | None = None,
):
    output = _analysis_artifact(service, child)
    if output is None:
        return [], []
    value = _json(service.artifacts, output, "Revision output")
    if revision.action is RevisionAction.reread_text:
        rows = value.get("texts", []) if isinstance(value, dict) else []
        rows = [row for row in rows if isinstance(row, dict) and row.get("text_id") == revision.target_ids[0]]
        if not rows and target_box is not None:
            candidates = []
            for row in value.get("texts", []) if isinstance(value, dict) else []:
                if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                    continue
                try:
                    box = _row_box(row)
                except PipelineError:
                    continue
                if (
                    box[0] < target_box[2]
                    and target_box[0] < box[2]
                    and box[1] < target_box[3]
                    and target_box[1] < box[3]
                ):
                    candidates.append((box, row))
            rows = [row for _, row in sorted(candidates, key=lambda item: (item[0][1], item[0][0]))]
            if rows:
                rows = [{"text": " ".join(row["text"] for row in rows)}]
        actions = [
            {"action": "set_text", "text_region_id": revision.target_ids[0], "text": row.get("text", "")}
            for row in rows
            if isinstance(row.get("text"), str)
        ]
        return actions, [output]
    if isinstance(value, dict) and isinstance(value.get("output"), dict):
        value = value["output"]
    corrections = value.get("correction_suggestions", []) if isinstance(value, dict) else []
    allowed_text_ids = target_text_ids or set(revision.target_ids)
    actions = [
        {"action": "set_text", "text_region_id": item["text_id"], "text": item["suggestion"]}
        for item in corrections
        if isinstance(item, dict)
        and item.get("text_id") in allowed_text_ids
        and isinstance(item.get("suggestion"), str)
    ]
    return actions, [output]


def _find_binding(service, request: UIAnalysisRequest) -> ReviewedLayoutBinding | None:
    metadata_ref = getattr(request.input, "metadata_ref", None)
    if metadata_ref is None:
        return None
    try:
        manifest = _json(service.artifacts, metadata_ref, "Input manifest")
    except PipelineError:
        return None

    def walk(value):
        if isinstance(value, Mapping):
            if "confirmed_binding" in value:
                try:
                    return ReviewedLayoutBinding.model_validate(value["confirmed_binding"])
                except Exception:
                    return None
            for child in value.values():
                found = walk(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = walk(child)
                if found is not None:
                    return found
        return None

    binding = walk(manifest)
    if binding is None and isinstance(manifest, Mapping):
        for source in manifest.get("sources", []):
            if not isinstance(source, Mapping):
                continue
            value = source.get("provenance_ref")
            if not isinstance(value, Mapping):
                continue
            try:
                provenance_ref = ArtifactRef.model_validate(value)
                binding = walk(_json(service.artifacts, provenance_ref, "Input provenance"))
            except Exception:
                continue
            if binding is not None:
                break
    return binding


def _revision_manifest(service, root, record, child, output_refs, request, revision_ref):
    target_text_ids = set(record.request.target_ids)
    target_box = None
    if record.request.action is RevisionAction.reread_text and record.texts_ref is not None:
        try:
            rows = _texts_value(service, record.texts_ref)
            target = next((row for row in rows if _row_id(row) == record.request.target_ids[0]), None)
            if target is not None:
                target_box = _row_box(target)
        except PipelineError:
            pass
    if record.layout_ref is not None:
        try:
            layout = _layout_value(service, record.layout_ref)
            for item in layout.get("elements", []):
                if not isinstance(item, Mapping) or item.get("element_id") not in record.request.target_ids:
                    continue
                for key in ("text_region_ids", "text_ids"):
                    values = item.get(key)
                    if isinstance(values, list):
                        target_text_ids.update(value for value in values if isinstance(value, str))
        except PipelineError:
            pass
    actions, evidence = _suggestion_payload(
        service,
        record.request,
        child,
        output_refs,
        target_text_ids=target_text_ids,
        target_box=target_box,
    )
    suggestions = []
    if not actions and record.request.action in {
        RevisionAction.adjust_segmentation,
        RevisionAction.regenerate,
    }:
        evidence = list(output_refs)
        suggestions.append(
            {
                "state": "read_only",
                "kind": "revision_candidate",
                "target_ids": record.request.target_ids,
                "evidence_refs": [ref.model_dump(mode="json") for ref in evidence],
            }
        )
    binding = _find_binding(service, request)
    if actions:
        if binding is not None:
            try:
                from .revision_review import save_suggestion

                suggestion = save_suggestion(
                    service,
                    binding,
                    sorted({item["text_region_id"] for item in actions if "text_region_id" in item}),
                    actions,
                    evidence,
                    evidence_task_ids=[root.task_id, child.task_id],
                )
                suggestions.append({"ref": suggestion.model_dump(mode="json"), "state": "saved"})
            except PipelineError as exc:
                suggestions.append({"state": "inapplicable", "reason": exc.code, "actions": actions})
        else:
            suggestions.append(
                {
                    "state": "read_only",
                    "actions": actions,
                    "evidence_refs": [ref.model_dump(mode="json") for ref in evidence],
                }
            )
    payload = {
        "schema_version": 1,
        "kind": "ui_revision_manifest",
        "task_id": root.task_id,
        "revision_ref": revision_ref.model_dump(mode="json"),
        "base_task_id": record.request.base_task_id,
        "base_fingerprint": record.request.base_fingerprint,
        "action": record.request.action,
        "target_ids": record.request.target_ids,
        "child_task_id": child.task_id,
        "child_fingerprint": child.fingerprint,
        "outputs": [ref.model_dump(mode="json") for ref in output_refs],
        "suggestions": suggestions,
        "proposal_only": record.request.action in {RevisionAction.reread_text, RevisionAction.review_region},
    }
    manifest = service.artifacts.put(
        root.task_id,
        "revision-" + revision_ref.sha256[:48],
        canonical_json(payload).encode(),
        role="revision_manifest",
        source_ids=[
            revision_ref.artifact_id,
            child.parameters["request_ref"]["artifact_id"],
            *[ref.artifact_id for ref in output_refs],
        ],
    )
    return manifest


class RevisionExecution:
    """Plan and advance one immutable revision request."""

    def __init__(self, service):
        self.service = service
        self.store = service.artifacts
        self.editing = EditingExecution(service)

    def plan(self, request: RevisionRequest | Mapping[str, Any]) -> RevisionPreparation:
        revision = RevisionRequest.model_validate(
            request.model_dump(mode="json") if hasattr(request, "model_dump") else request
        )
        root, base = _root_and_base(self.service, revision)
        root_request = _root_request(self.service, root)
        if base.task_id != root.task_id:
            self.editing.validate_current(base)
        require_texts = revision.action in {RevisionAction.reread_text, RevisionAction.review_region}
        selection_ref, selection, layout_ref, layout, canonical_ref, texts_ref = _frozen_inputs(
            self.service, root, root_request, require_texts=require_texts
        )
        text_rows = _texts_value(self.service, texts_ref) if texts_ref is not None else []
        boxes = _target_geometry(revision.action, revision.target_ids, layout, text_rows)
        elements = _element_boxes(layout)
        source = _target_source(selection, revision.target_ids[0], next(iter(boxes.values())), elements)
        if any(
            _target_source(selection, target, box, elements).source_id != source.source_id
            for target, box in boxes.items()
        ):
            raise _error("source_scope")
        selection_revision = _selection_revision(self.service, root, selection_ref)
        base_outputs = _outputs(self.service, base.task_id)
        role_nodes = {
            "canonical": RevisionNode.normalize,
            "texts": RevisionNode.ocr,
            "review_texts": RevisionNode.ocr,
            "analysis": RevisionNode.analyze,
            "layout": RevisionNode.layout,
            "review_layout": RevisionNode.layout,
            "rect_crop": RevisionNode.crop,
            "segmentation": RevisionNode.segmentation,
            "glyph": RevisionNode.glyphs,
            "glyphs": RevisionNode.glyphs,
            "mask": RevisionNode.mask,
            "binary_mask": RevisionNode.mask,
            "edit_mask": RevisionNode.mask,
            "image": RevisionNode.inpaint,
            "manifest": RevisionNode.manifest,
        }
        impact_artifacts = []
        for ref in base_outputs[:64]:
            node = role_nodes.get(ref.role)
            if node is None:
                continue
            target_ids = _artifact_targets(self.service, ref)
            # Text revisions invalidate artifacts for elements linked to that
            # text, even though their public IDs use different namespaces.
            for element in layout.get("elements", []):
                if element.get("element_id") in target_ids:
                    target_ids.extend(element.get("text_region_ids", element.get("text_ids", [])))
            impact_artifacts.append(
                RevisionArtifact(
                    node=node,
                    artifact_ref=ref,
                    target_ids=list(dict.fromkeys(target_ids)),
                )
            )
        known_text_ids = {value for value in (_row_id(row) for row in text_rows) if value}
        impact = project_revision(
            revision,
            impact_artifacts,
            known_target_ids=set(elements) | known_text_ids,
        )
        revision_ref = self.store.put(
            root.task_id,
            "revision-request-" + digest(revision.model_dump(mode="json"))[:48],
            revision.model_dump_json().encode(),
            role="revision_request",
        )
        child = None
        artifacts: list[ArtifactRef] = [revision_ref]
        if revision.action in {RevisionAction.reread_text, RevisionAction.review_region}:
            child_id = "ui-revision-" + digest({"root": root.task_id, "revision": revision_ref.sha256})[:40]
            child, local_artifacts = _local_request(
                self.service,
                root,
                root_request,
                revision,
                selection_ref,
                selection,
                source,
                canonical_ref,
                layout_ref,
                texts_ref,
                _view_box(revision.action, boxes, root_request.resources.vlm_longest_edge),
                source_id=source.source_id,
                selection_revision=selection_revision,
                revision_ref=revision_ref,
                child_id=child_id,
            )
            artifacts.extend(local_artifacts)
        elif revision.action is RevisionAction.adjust_segmentation:
            if base.workflow_type != "ui_segmentation":
                raise _error("invalid_plan")
            state, _, reason = _child_operation_state(self.service, base)
            if state != "succeeded":
                raise _error("dependency_not_ready", reason or "Segmentation has not succeeded")
            child, adjustment_artifacts = _adjust_segmentation_child(
                self.service,
                root,
                base,
                root_request,
                selection,
                selection_ref,
                source.source_id,
                revision,
                selection_revision,
                revision_ref,
            )
            artifacts.extend(adjustment_artifacts)
        else:
            if base.workflow_type != "ui_inpaint" or _child_operation_state(self.service, base)[0] != "succeeded":
                raise _error("dependency_not_ready")
            parent_id = base.parameters.get("parent_task_id")
            if not isinstance(parent_id, str):
                raise _error("invalid_plan")
            segment = _plan(self.service, parent_id, self.service.ledger.plan(parent_id).fingerprint)
            self.editing.validate_current(segment)
            from ..schemas.agent import MaskedGenerationPlan

            masked = MaskedGenerationPlan.model_validate_json(self.store.read(_request_ref(base)))
            changed_parameters = dict(masked.parameters)
            changed_parameters.update(revision.parameters)
            changed = masked.model_copy(
                update={
                    "task_id": "ui-revision-inpaint-" + revision_ref.sha256[:40],
                    "parameters": changed_parameters,
                }
            )
            prepared = self.editing.plan_inpaint(
                segment, changed, selection_ref=selection_ref, budget=base.envelope.budget
            )
            child = prepared.child
            if child is None:
                raise _error("dependency_not_ready")
            artifacts.extend(prepared.artifacts)
        record = _RevisionPlanRecord(
            root_task_id=root.task_id,
            child_task_id=child.task_id,
            child_fingerprint=child.fingerprint,
            request_ref=revision_ref,
            request=revision,
            base_plan=base,
            base_outputs=base_outputs,
            child=child,
            impact=impact,
            selection_ref=selection_ref,
            selection_revision=selection_revision,
            canonical_ref=canonical_ref,
            layout_ref=layout_ref,
            texts_ref=texts_ref,
            source_id=source.source_id,
        )
        plan_ref = self.store.put(
            root.task_id,
            "revision-" + digest(revision.model_dump(mode="json"))[:40],
            record.model_dump_json().encode(),
            role="revision_plan",
            source_ids=[revision_ref.artifact_id, child.parameters["request_ref"]["artifact_id"]],
        )
        state = "awaiting_approval" if child is not None else "failed"
        return RevisionPreparation(
            root_task_id=root.task_id,
            revision_ref=plan_ref,
            state=state,
            child=child,
            impact=impact,
            artifacts=artifacts + [plan_ref],
            selection_ref=selection_ref,
            selection_revision=selection_revision,
            source_id=source.source_id,
        )

    def prepare(self, root: PipelinePlan | str, revision_ref: ArtifactRef | Mapping[str, Any]) -> EditingPreparation:
        root_plan = self.service.ledger.plan(root) if isinstance(root, str) else root
        root_plan = _plan(self.service, root_plan.task_id, root_plan.fingerprint)
        ref = _ref_role(revision_ref, "revision_plan")
        if ref.task_id != root_plan.task_id:
            raise _error("artifact_scope")
        record = _RevisionPlanRecord.model_validate_json(self.store.read(ref))
        if record.root_task_id != root_plan.task_id:
            raise _error("plan_changed")
        stored_base = _plan(self.service, record.base_plan.task_id, record.base_plan.fingerprint)
        if stored_base != record.base_plan:
            raise _error("plan_changed")
        if record.child_task_id != record.child.task_id or record.child_fingerprint != record.child.fingerprint:
            raise _error("plan_changed")
        child = _plan(self.service, record.child.task_id, record.child.fingerprint)
        if child != record.child:
            raise _error("plan_changed")
        state, artifacts, reason = _child_operation_state(self.service, child)
        selection_ref = record.selection_ref
        if state == "awaiting_reconciliation":
            return EditingPreparation(
                root_task_id=record.root_task_id,
                state=state,
                child=child,
                artifacts=artifacts,
                reason=reason,
                selection_ref=selection_ref,
                selection_revision=record.selection_revision,
                source_id=record.source_id,
            )
        if state in {"awaiting_approval", "failed"}:
            return EditingPreparation(
                root_task_id=record.root_task_id,
                state=state,
                child=child,
                artifacts=artifacts,
                reason=reason,
                selection_ref=selection_ref,
                selection_revision=record.selection_revision,
                source_id=record.source_id,
            )
        root_request = _root_request(self.service, root_plan)
        if child.workflow_type in {"ui_segmentation", "ui_inpaint"}:
            segment_released = any(
                _release_confirmed(operation)
                for operation in self.service.ledger.list_operations(child.task_id)
                if operation.state == "succeeded"
            )
            if not segment_released:
                return EditingPreparation(
                    root_task_id=record.root_task_id,
                    state="awaiting_reconciliation",
                    child=child,
                    artifacts=artifacts,
                    reason="GPU resource release has not been confirmed",
                    selection_ref=selection_ref,
                    selection_revision=record.selection_revision,
                    source_id=record.source_id,
                )
        if record.request.action is RevisionAction.adjust_segmentation:
            from .revision_segmentation import merge_revision_segmentation

            artifacts = merge_revision_segmentation(self.service, record.base_plan, child, artifacts)
            if root_request.output_mode == "decompose":
                from .editing_outputs import finalize_decomposition

                artifacts = finalize_decomposition(self.service, root_plan, child, artifacts)
            else:
                prepared = self.editing._default_inpaint_plan(
                    child,
                    root_plan,
                    root_request,
                    selection_ref=record.selection_ref,
                    budget=root_plan.envelope.budget,
                    segment_artifacts=artifacts,
                )
                if prepared.state != "succeeded":
                    return prepared
                artifacts = prepared.artifacts
                child = prepared.child or child
        manifest = _revision_manifest(
            self.service,
            root_plan,
            record,
            child,
            artifacts,
            root_request,
            ref,
        )
        return EditingPreparation(
            root_task_id=record.root_task_id,
            state="succeeded",
            child=child,
            artifacts=[*artifacts, manifest],
            selection_ref=selection_ref,
            selection_revision=record.selection_revision,
            source_id=record.source_id,
        )


__all__ = ["RevisionExecution", "RevisionPreparation"]
