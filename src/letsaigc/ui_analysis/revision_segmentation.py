"""Immutable merge of prompt scoped SAM revision outputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, PipelinePlan, canonical_json, operation_id
from ..vision.segmentation import SegmentationAsset, SegmentationBundle


def _error(code: str, message: str) -> PipelineError:
    return PipelineError(code, message)


def _ref(value: ArtifactRef | Mapping[str, object]) -> ArtifactRef:
    try:
        return value if isinstance(value, ArtifactRef) else ArtifactRef.model_validate(value)
    except Exception as exc:
        raise _error("artifact_scope", "Segmentation artifact reference is invalid") from exc


def _read_bundle(service, ref: ArtifactRef, plan: PipelinePlan) -> SegmentationBundle:
    from ..schemas.ui import UISegmentationRequest

    task_id = plan.task_id
    if ref.role != "segmentation" or ref.task_id != task_id:
        raise _error("artifact_scope", "Segmentation output is outside its child scope")
    try:
        bundle = SegmentationBundle.model_validate_json(service.artifacts.read(ref))
    except PipelineError:
        raise
    except Exception as exc:
        raise _error("artifact_changed", "Segmentation bundle is invalid") from exc
    if bundle.task_id != task_id or bundle.status != "ready":
        raise _error("dependency_not_ready", "Segmentation output is not terminal and ready")
    request = UISegmentationRequest.model_validate_json(service.artifacts.read(_ref(plan.parameters["request_ref"])))
    if (bundle.operation_id, bundle.canonical_ref, bundle.selection_ref, bundle.selection_revision,
        bundle.selection_hash, bundle.model_snapshot_ref, bundle.model_digest) != (
        operation_id(plan, "segment", 0), request.canonical_ref, request.selection_ref,
        request.selection_revision, request.selection_hash, request.model_snapshot_ref,
        request.model_snapshot_ref.sha256,
    ):
        raise _error("input_changed", "Segmentation output differs from its approved request")
    if any(item.status != "ready" for item in bundle.items):
        raise _error("dependency_not_ready", "Segmentation contains an unready target")
    ids = [item.element_id for item in bundle.items]
    if len(ids) != len(set(ids)):
        raise _error("artifact_changed", "Segmentation output contains duplicate targets")
    refs = [
        bundle.canonical_ref,
        bundle.selection_ref,
        bundle.model_snapshot_ref,
        *[
            ref
            for item in bundle.items
            for ref in (
                item.rect_crop_ref,
                item.contour_mask_ref,
                item.estimated_alpha_ref,
                item.visible_crop_ref,
            )
        ],
    ]
    if any(ref.task_id != task_id for ref in refs):
        raise _error("artifact_scope", "Segmentation bundle contains cross child references")
    for value in refs:
        service.artifacts.read(value)
    for item in bundle.items:
        if item.canonical_sha256 != request.canonical_ref.sha256 or any(
            value.operation_id != bundle.operation_id for value in _asset_refs(item)
        ):
            raise _error("artifact_scope", "Segmentation asset differs from its operation")
    return bundle


def _successful_ref(service, plan: PipelinePlan, artifacts: Sequence[ArtifactRef]) -> ArtifactRef:
    candidates = [ref for ref in artifacts if ref.role == "segmentation"]
    for operation in service.ledger.list_operations(plan.task_id):
        if operation.state != "succeeded":
            continue
        candidates.extend(
            _ref(value)
            for value in operation.result.get("artifacts", [])
            if isinstance(value, Mapping) and value.get("role") == "segmentation"
        )
    unique: dict[tuple[str, str], ArtifactRef] = {(ref.task_id, ref.sha256): ref for ref in candidates}
    if len(unique) != 1:
        raise _error("dependency_not_ready", "Exactly one successful segmentation output is required")
    return next(iter(unique.values()))


def _asset_refs(item: SegmentationAsset) -> tuple[ArtifactRef, ...]:
    return (
        item.rect_crop_ref,
        item.contour_mask_ref,
        item.estimated_alpha_ref,
        item.visible_crop_ref,
    )


def _prompt_ids(service, plan: PipelinePlan) -> set[str]:
    from ..schemas.ui import UISegmentationRequest

    try:
        request_ref = _ref(plan.parameters["request_ref"])
        request = UISegmentationRequest.model_validate_json(service.artifacts.read(request_ref))
    except Exception as exc:
        raise _error("artifact_changed", "Segmentation request is unavailable") from exc
    return {prompt.element_id for prompt in request.prompts}


def merge_revision_segmentation(
    service,
    base: PipelinePlan,
    child: PipelinePlan,
    artifacts: list[ArtifactRef],
) -> list[ArtifactRef]:
    """Merge a target-only SAM output into a durable complete bundle.

    The base and revision bundles remain available as evidence.  Only the
    merged bundle is substituted for downstream reconstruction; untouched
    assets are copied into the revision child scope with the same content hash
    and artifact identity.
    """

    if (
        base.workflow_type != "ui_segmentation"
        or child.workflow_type != "ui_segmentation"
        or base.task_id == child.task_id
    ):
        raise _error("invalid_plan", "Segmentation merge requires SAM child plans")
    base_ref = _successful_ref(service, base, [])
    parent = service.ledger.plan(base.parameters["parent_task_id"])
    if parent.workflow_type == "ui_segmentation":
        # A revision can itself be the explicit base of the next revision.
        # Recover its complete projection without changing raw operation evidence.
        previous = merge_revision_segmentation(service, parent, base, [base_ref])
        base_ref = next(ref for ref in previous if ref.role == "segmentation")
    child_ref = _successful_ref(service, child, artifacts)
    base_bundle = _read_bundle(service, base_ref, base)
    child_bundle = _read_bundle(service, child_ref, child)
    if (
        base_bundle.canonical_sha256 != child_bundle.canonical_sha256
        or base_bundle.canonical_ref.sha256 != child_bundle.canonical_ref.sha256
        or base_bundle.model_digest != child_bundle.model_digest
        or base_bundle.model_snapshot_ref.sha256 != child_bundle.model_snapshot_ref.sha256
    ):
        raise _error("input_changed", "Segmentation revision changed canonical or model identity")

    base_items = {item.element_id: item for item in base_bundle.items}
    child_items = {item.element_id: item for item in child_bundle.items}
    requested_ids = _prompt_ids(service, child)
    if (
        not child_items
        or not requested_ids
        or requested_ids != set(child_items)
        or not requested_ids <= set(base_items)
        or not set(child_items) <= set(base_items)
    ):
        raise _error("unknown_target", "Segmentation revision returned an unknown target")

    merged_items: list[SegmentationAsset] = []
    copied_refs: list[ArtifactRef] = []
    operation_id = child_bundle.operation_id
    for element_id, base_item in base_items.items():
        revised = child_items.get(element_id)
        if revised is not None:
            merged_items.append(revised)
            continue
        copied: list[ArtifactRef] = []
        for old_ref in _asset_refs(base_item):
            copied_ref = service.artifacts.put(
                child.task_id,
                operation_id,
                service.artifacts.read(old_ref),
                role=old_ref.role,
                media_type=old_ref.media_type,
                source_ids=[old_ref.artifact_id],
            )
            copied.append(copied_ref)
            copied_refs.append(copied_ref)
        merged_items.append(
            base_item.model_copy(
                update={
                    "rect_crop_ref": copied[0],
                    "contour_mask_ref": copied[1],
                    "estimated_alpha_ref": copied[2],
                    "visible_crop_ref": copied[3],
                }
            )
        )

    merged_bundle = child_bundle.model_copy(
        update={
            "items": merged_items,
            "canonical_sha256": base_bundle.canonical_sha256,
            "model_digest": base_bundle.model_digest,
        }
    )
    merged_ref = service.artifacts.put(
        child.task_id,
        operation_id,
        canonical_json(merged_bundle).encode(),
        role="segmentation",
        source_ids=[base_ref.artifact_id, child_ref.artifact_id],
    )
    output: list[ArtifactRef] = []
    seen: set[tuple[str, str, str]] = set()
    for ref in [*artifacts, *copied_refs, merged_ref]:
        key = (ref.task_id, ref.sha256, ref.role)
        if ref.role == "segmentation" and ref != merged_ref:
            # Raw bundles remain in their original operation records.
            continue
        if key not in seen:
            output.append(ref)
            seen.add(key)
    return output


__all__ = ["merge_revision_segmentation"]
