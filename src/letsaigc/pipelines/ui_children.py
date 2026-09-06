"""Small, provider-free helpers for the UI editing child boundary.

T020 deliberately stops at trusted registration and accounting.  This module
validates the immutable material that crosses that boundary; SAM/Comfy
adapters are registered by later tasks.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..schemas.agent import TaskBudget
from ..schemas.pipeline import ArtifactRef, PipelinePlan, canonical_json, validate_payload
from .errors import PipelineError

CHILD_PURPOSES = {"segmentation", "inpaint"}
CHILD_WORKFLOWS = {
    "segmentation": ("ui_segmentation", "ui.segment"),
    "inpaint": ("ui_inpaint", "ui.inpaint"),
}


def is_child_plan(plan: PipelinePlan) -> bool:
    return plan.workflow_type in {item[0] for item in CHILD_WORKFLOWS.values()}


def child_definition(purpose: str) -> tuple[str, str]:
    try:
        return CHILD_WORKFLOWS[purpose]
    except KeyError as exc:
        raise PipelineError("invalid_child", "Unknown UI child purpose") from exc


def capability_for_plan(plan: PipelinePlan) -> str:
    for workflow, capability in CHILD_WORKFLOWS.values():
        if plan.workflow_type == workflow:
            return capability
    raise PipelineError("prohibited_capability")


def budget_json(budget: TaskBudget) -> str:
    return canonical_json(budget.model_dump(mode="json"))


def budget_subset(child: TaskBudget, parent: TaskBudget) -> bool:
    return all(
        getattr(child, field) <= getattr(parent, field)
        for field in (
            "max_total_cost_usd",
            "max_iteration_cost_usd",
            "max_total_gpu_minutes",
            "max_iteration_gpu_minutes",
            "max_revisions",
        )
    )


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", value):
        raise PipelineError("invalid_child", f"Invalid {label}")
    return value


def load_json(artifacts, ref: ArtifactRef, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(artifacts.read(ref))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise PipelineError("invalid_child", f"Invalid {label} artifact") from exc
    if not isinstance(value, dict):
        raise PipelineError("invalid_child", f"{label} must be an object")
    validate_payload(value)
    return value


def validate_selection(
    artifacts,
    ref: ArtifactRef,
    *,
    source_ids: list[str],
    root_task_id: str,
    source_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    if ref.role != "selection":
        raise PipelineError("invalid_selection", "A child selection must use the selection role")
    if ref.task_id != root_task_id:
        raise PipelineError("artifact_scope", "Selection is outside the root task scope")
    value = load_json(artifacts, ref, label="selection")
    try:
        from ..schemas.ui import UISelection

        parsed_selection = UISelection.model_validate(value)
    except Exception as exc:
        raise PipelineError("invalid_selection", "Selection does not match its runtime DTO") from exc
    if value.get("schema_version") != 1 or not isinstance(value.get("sources"), list):
        raise PipelineError("invalid_selection", "Unsupported selection content")
    if not 1 <= len(value["sources"]) <= 10:
        raise PipelineError("invalid_selection", "Selection source count is outside the approved range")
    seen_sources: set[str] = set()
    expected = set(source_ids)
    for source_model, source in zip(parsed_selection.sources, value["sources"], strict=True):
        if not isinstance(source, dict):
            raise PipelineError("invalid_selection")
        source_id = _identifier(source.get("source_id"), "selection source")
        if source_id in seen_sources:
            raise PipelineError("invalid_selection", "Selection contains duplicate sources")
        seen_sources.add(source_id)
        if source_id not in expected:
            raise PipelineError("source_scope", "Selection references an unapproved source")
        if not isinstance(source.get("original_sha256"), str) or not re.fullmatch(
            r"[a-f0-9]{64}", source["original_sha256"]
        ):
            raise PipelineError("invalid_selection", "Selection source hash is invalid")
        if source_hashes is not None and source["original_sha256"] != source_hashes.get(source_id):
            raise PipelineError("selection_conflict", "Selection source hash differs from the root image")
        regions = source.get("target_regions")
        if not isinstance(regions, list) or not 1 <= len(regions) <= 64:
            raise PipelineError("invalid_selection", "Selection region count is invalid")
        for region in regions:
            if not isinstance(region, dict) or region.get("kind") not in {"bbox", "element"}:
                raise PipelineError("invalid_selection", "Selection region is invalid")
            if region["kind"] == "bbox":
                box = region.get("xyxy")
                if (
                    not isinstance(box, list)
                    or len(box) != 4
                    or any(type(item) is not int or item < 0 for item in box)
                    or box[2] <= box[0]
                    or box[3] <= box[1]
                ):
                    raise PipelineError("invalid_selection", "Selection bbox is invalid")
        for field in ("keep_elements", "remove_elements"):
            values = source.get(field, [])
            if not isinstance(values, list) or len(values) != len(set(values)):
                raise PipelineError("invalid_selection", "Selection element list is invalid")
        if source_model.layout_ref is not None:
            layout = source_model.layout_ref
            if layout.task_id != root_task_id:
                raise PipelineError("artifact_scope", "Selection layout is outside the root scope")
            artifacts.read(layout)
    if seen_sources != expected:
        raise PipelineError("source_scope", "Selection does not cover the approved sources")
    return value


def validate_request(
    artifacts,
    ref: ArtifactRef,
    *,
    task_id: str,
    purpose: str,
    selection_ref: ArtifactRef,
    selection_revision: int,
) -> dict[str, Any]:
    if ref.task_id != task_id or ref.role != "request":
        raise PipelineError("artifact_scope", "Child request must be saved in the child scope")
    value = load_json(artifacts, ref, label="child request")
    if value.get("schema_version") not in {1, 2}:
        raise PipelineError("invalid_child", "Unsupported child request schema")
    if purpose == "segmentation":
        try:
            from ..schemas.ui import UISegmentationRequest

            parsed = UISegmentationRequest.model_validate(value)
        except Exception as exc:
            raise PipelineError("invalid_child", "Segmentation request does not match its DTO") from exc
        for item in (parsed.canonical_ref, parsed.selection_ref, parsed.model_snapshot_ref):
            if item.task_id != task_id:
                raise PipelineError("artifact_scope", "Segmentation references must belong to the child")
            artifacts.read(item)
        if parsed.selection_revision != selection_revision or parsed.selection_ref.sha256 != selection_ref.sha256:
            raise PipelineError("selection_conflict", "Segmentation selection does not match the child")
    if purpose == "inpaint":
        binding = value.get("image_mask")
        if not isinstance(binding, dict):
            raise PipelineError("invalid_child", "Inpaint request requires image_mask")
        try:
            from ..schemas.agent import MaskedGenerationPlan

            parsed = MaskedGenerationPlan.model_validate(value)
        except Exception as exc:
            raise PipelineError("invalid_child", "Inpaint request does not match its DTO") from exc
        binding = parsed.image_mask.model_dump(mode="json")
        if binding.get("selection_hash") != selection_ref.sha256:
            raise PipelineError("selection_conflict", "Request selection hash does not match the frozen selection")
        if binding.get("selection_revision") != selection_revision:
            raise PipelineError("selection_conflict", "Request selection revision does not match the child")
        if binding.get("mask_role") != "edit_mask" or binding.get("recipe_supports_mask") is not True:
            raise PipelineError("invalid_child", "Inpaint mask contract is not supported")
        refs = []
        for key in (
            "image_ref",
            "mask_ref",
            "canonical_ref",
            "canonical_edit_mask_ref",
            "selection_ref",
            "view_transform_ref",
        ):
            try:
                item = ArtifactRef.model_validate(binding[key])
            except Exception as exc:
                raise PipelineError("invalid_child", "Inpaint request has an invalid artifact reference") from exc
            refs.append(item)
            if item.task_id != task_id:
                raise PipelineError("artifact_scope", "Inpaint references must belong to the child")
            artifacts.read(item)
        if refs[4].role != "selection" or refs[4].sha256 != selection_ref.sha256:
            raise PipelineError("selection_conflict", "Inpaint selection reference differs from the child selection")
        if refs[1].role != "edit_mask" or refs[3].role != "edit_mask":
            raise PipelineError("invalid_child", "Inpaint masks must use the edit_mask role")
    return value


def validate_child_plan(plan: PipelinePlan) -> None:
    workflow, capability = child_definition(
        "segmentation" if plan.workflow_type == "ui_segmentation" else "inpaint"
    )
    if plan.workflow_type != workflow or plan.generation_plan is not None:
        raise PipelineError("invalid_plan", "UI child plan registration is invalid")
    if plan.envelope.stage != "generation" or plan.envelope.allowed_capabilities != [capability]:
        raise PipelineError("prohibited_capability", "UI child capability is not registered for this workflow")
    if not {"parent_task_id", "root_task_id", "purpose", "request_ref", "selection_ref", "selection_revision"} <= set(
        plan.parameters
    ):
        raise PipelineError("invalid_plan", "UI child plan is missing its frozen binding")
    refs = [*plan.inputs]
    if any(ref.task_id != plan.task_id for ref in refs):
        raise PipelineError("artifact_scope", "UI child inputs must belong to the child")
