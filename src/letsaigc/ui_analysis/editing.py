"""Provider-free planning for the UI editing child workflow.

This module is intentionally a domain coordinator rather than an execution
adapter.  It reads frozen root outputs and selection artifacts, copies the
small immutable inputs into a child scope, and registers a child through
``PipelineService.ui_child_plan``.  It never loads a model, calls OCR/VLM/SAM,
compiles a recipe, or submits an operation.

The Temporal layer can call :meth:`EditingExecution.prepare` repeatedly.  The
task id and content-addressed artifacts make that call idempotent, while the
ledger lookup prevents a newer selection from quietly replacing an in-flight
child.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from PIL import Image, ImageChops, ImageDraw
from pydantic import Field

from ..pipelines.errors import PipelineError
from ..schemas.agent import ExecutionEnvelope, GenerationIntent, MaskedGenerationPlan, TaskBudget
from ..schemas.pipeline import ArtifactRef, PipelineModel, PipelinePlan, canonical_json, digest
from ..schemas.ui import (
    UIAnalysisRequest,
    UISegmentationPrompt,
    UISegmentationRequest,
    UISelection,
    UISelectionSource,
    UIStepBinding,
)
from . import selection as selection_tools


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


EditingState = Literal[
    "awaiting_selection",
    "awaiting_approval",
    "awaiting_reconciliation",
    "succeeded",
    "failed",
]


class EditingPreparation(PipelineModel):
    """Stable projection returned by ``prepare`` and ``status``."""

    root_task_id: str
    state: EditingState
    child: PipelinePlan | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=64)
    reason: str | None = None
    selection_ref: ArtifactRef | None = None
    selection_revision: int | None = Field(default=None, ge=0, strict=True)
    source_id: str | None = None


@dataclass(frozen=True)
class EditMaskResult:
    """Materialized canonical edit mask and its deterministic status."""

    mask: Image.Image
    status: Literal["ready", "no_edit_pixels"]
    reason: str | None = None


def _as_plan(service, value: PipelinePlan | str) -> PipelinePlan:
    if isinstance(value, PipelinePlan):
        return value
    if isinstance(value, str):
        return service.ledger.plan(value)
    raise TypeError("Expected a PipelinePlan or task id")


def _json(store, ref: ArtifactRef, *, label: str) -> Any:
    try:
        return json.loads(store.read(ref))
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise PipelineError("artifact_changed", f"{label} is not valid JSON") from exc


def _ref(value: Any, *, role: str | None = None) -> ArtifactRef:
    try:
        result = value if isinstance(value, ArtifactRef) else ArtifactRef.model_validate(value)
    except Exception as exc:
        raise PipelineError("artifact_scope", "A frozen editing artifact reference is invalid") from exc
    if role is not None and result.role != role:
        raise PipelineError("artifact_scope", f"Editing artifact must have role {role}")
    return result


def _budget(value: TaskBudget | Mapping[str, Any] | None, fallback: TaskBudget) -> TaskBudget:
    if value is None:
        return fallback
    try:
        return TaskBudget.model_validate(value.model_dump(mode="json") if hasattr(value, "model_dump") else value)
    except Exception as exc:
        raise PipelineError("invalid_budget", "Editing child budget is invalid") from exc


def _selection_revision(service, root: PipelinePlan, selection_ref: ArtifactRef) -> int:
    """Read the immutable selection catalog revision when one exists."""
    catalog = selection_tools.selection_catalog(service.artifacts, root.task_id)
    if catalog and catalog.selection_ref and catalog.selection_ref.sha256 == selection_ref.sha256:
        return catalog.revision
    # Explicit bound selections are revision zero unless they were recorded in
    # the immutable catalog.  This is the legacy-compatible first selection.
    return 0


def _selection_ref(service, root: PipelinePlan, request: UIAnalysisRequest, explicit: ArtifactRef | None):
    if explicit is not None:
        ref = _ref(explicit, role="selection")
    elif request.selection_mode == "bound" and request.selection_ref is not None:
        ref = _ref(request.selection_ref, role="selection")
    else:
        catalog = selection_tools.selection_catalog(service.artifacts, root.task_id)
        ref = catalog.selection_ref if catalog and catalog.selection_ref else None
        # A single trusted proposal is the default recommendation.  It is
        # already validated and immutable, so using its selection artifact
        # here does not perform model work or consume approval.
        if ref is None and catalog and len(catalog.candidates) == 1 and catalog.state == "awaiting_approval":
            ref = catalog.candidates[0].selection_ref
        if ref is None:
            candidates = [] if catalog is None else [x.preview_ref for x in catalog.candidates]
            return None, candidates
    if ref.task_id != root.task_id:
        raise PipelineError("artifact_scope", "Selection must be frozen in the root task scope")
    service.artifacts.read(ref)
    return ref, []


def _frozen_sam_snapshot(service, root: PipelinePlan) -> ArtifactRef:
    """Persist the platform SAM lock as a model snapshot, without loading it."""
    try:
        from ..vision.sam_loader import load_sam_settings

        _config, lock = load_sam_settings()
    except Exception as exc:
        raise PipelineError("capability_not_ready", "SAM model lock is unavailable") from exc
    return service.artifacts.put(
        root.task_id,
        "plan",
        canonical_json(lock).encode(),
        role="model_snapshot",
        source_ids=[ref.artifact_id for ref in root.inputs],
    )


def _successful_outputs(service, task_id: str) -> list[ArtifactRef]:
    result: dict[tuple[str, str], ArtifactRef] = {}
    for operation in service.ledger.list_operations(task_id):
        if operation.state != "succeeded":
            continue
        for item in operation.result.get("artifacts", []):
            try:
                ref = ArtifactRef.model_validate(item)
                service.artifacts.read(ref)
            except Exception as exc:
                raise PipelineError("artifact_changed", "Root execution output is invalid") from exc
            result[(ref.role, ref.sha256)] = ref
    return list(result.values())


def _root_originals(service, root: PipelinePlan, request: UIAnalysisRequest) -> list[ArtifactRef]:
    """Resolve immutable originals from manual, reviewed, or acquired roots."""
    refs = [ref for ref in root.inputs if ref.role == "original"]
    manifests: list[ArtifactRef] = []
    input_spec = request.input
    metadata_ref = getattr(input_spec, "metadata_ref", None)
    if metadata_ref is not None:
        manifests.append(metadata_ref)
    for operation in service.ledger.list_operations(root.task_id):
        if operation.state != "succeeded":
            continue
        manifests.extend(
            _ref(item, role="input_manifest")
            for item in operation.result.get("artifacts", [])
            if isinstance(item, Mapping) and item.get("role") == "input_manifest"
        )
    try:
        from ..schemas.ui_provider import UIInputManifest

        for manifest_ref in manifests:
            manifest = UIInputManifest.model_validate_json(service.artifacts.read(manifest_ref))
            refs.extend(source.original_ref for source in manifest.sources)
    except Exception as exc:
        if manifests:
            raise PipelineError("artifact_changed", "Frozen input manifest is unavailable") from exc
    result: dict[str, ArtifactRef] = {}
    for ref in refs:
        if ref.task_id == root.task_id and ref.role in {"original", "canonical"}:
            result.setdefault(ref.sha256, ref)
    return list(result.values())


def _sources(service, root: PipelinePlan, request: UIAnalysisRequest, selection: UISelection) -> dict[str, ArtifactRef]:
    """Map selection source ids to the frozen original refs in the root."""
    by_hash = {ref.sha256: ref for ref in _root_originals(service, root, request)}
    result: dict[str, ArtifactRef] = {}
    for source in selection.sources:
        original = by_hash.get(source.original_sha256)
        if original is None:
            # A provision result/input manifest can be the only place where a
            # source id is recorded; callers may still provide root originals.
            for ref in root.inputs:
                if ref.role == "original" and ref.sha256 == source.original_sha256:
                    original = ref
                    break
        if original is None:
            raise PipelineError("source_scope", "Selection source is not in the frozen root inputs")
        result[source.source_id] = original
    return result


def _canonical_by_source(
    service, root: PipelinePlan, source_ids: Iterable[str], outputs: Sequence[ArtifactRef], kwargs
):
    """Resolve canonical refs from explicit input or completed normalize output."""
    explicit = kwargs.get("canonical_refs")
    if explicit is None:
        explicit = kwargs.get("canonical_ref")
    if explicit is not None:
        if isinstance(explicit, Mapping):
            values = {str(k): _ref(v, role="canonical") for k, v in explicit.items()}
        else:
            values = {next(iter(source_ids)): _ref(explicit, role="canonical")}
        for ref in values.values():
            service.artifacts.read(ref)
        return values
    result: dict[str, ArtifactRef] = {}
    for ref in outputs:
        if ref.role != "canonical":
            continue
        # A canonical image itself has no source id in its ref.  The index is
        # authoritative and is checked against the actual ref hash.
        source_list = list(source_ids)
        result.setdefault(source_list[0] if len(source_list) == 1 else ref.artifact_id, ref)
    for ref in outputs:
        if ref.role != "canonical_index":
            continue
        value = _json(service.artifacts, ref, label="Canonical index")
        item = value.get("canonical") if isinstance(value, Mapping) else None
        if not isinstance(item, Mapping):
            continue
        canonical = _ref(item, role="canonical")
        source_id = item.get("source_id")
        if isinstance(source_id, str):
            if canonical.sha256 != next((x.sha256 for x in outputs if x.role == "canonical"), canonical.sha256):
                raise PipelineError("artifact_changed", "Canonical index does not match its output")
            result[source_id] = canonical
    if not result:
        raise PipelineError("dependency_not_ready", "Canonical output is not ready")
    return result


def _model_binding_ref(request: UIAnalysisRequest, name: str, *, role: str) -> ArtifactRef | None:
    value = request.model_bindings.get(name)
    if value is None:
        return None
    return _ref(value, role=role)


def _layout_boxes(store, source: UISelectionSource) -> dict[str, tuple[int, int, int, int]]:
    if source.layout_ref is None:
        return {}
    layout = _json(store, source.layout_ref, label="Frozen layout")
    elements = layout.get("elements") if isinstance(layout, Mapping) else None
    if not isinstance(elements, list):
        raise PipelineError("artifact_changed", "Frozen layout has no elements")
    result: dict[str, tuple[int, int, int, int]] = {}
    for item in elements:
        if not isinstance(item, Mapping) or not isinstance(item.get("element_id"), str):
            raise PipelineError("artifact_changed", "Frozen layout element is invalid")
        box = item.get("bbox")
        if not isinstance(box, (list, tuple)) or len(box) != 4 or any(type(x) is not int for x in box):
            raise PipelineError("artifact_changed", "Frozen layout box is invalid")
        result[item["element_id"]] = tuple(box)  # type: ignore[assignment]
    return result


def _prompts(store, selection: UISelectionSource, supplied: Sequence[UISegmentationPrompt | Mapping[str, Any]] | None):
    if supplied:
        try:
            return [
                UISegmentationPrompt.model_validate(x.model_dump(mode="json") if hasattr(x, "model_dump") else x)
                for x in supplied
            ]
        except Exception as exc:
            raise PipelineError("invalid_selection", "Segmentation prompts are invalid") from exc
    elements = _layout_boxes(store, selection)
    prompts = []
    for index, region in enumerate(selection.target_regions, 1):
        if region.kind == "element":
            try:
                box = elements[region.element_id]
            except KeyError as exc:
                raise PipelineError("selection_conflict", "Target element is absent from the frozen layout") from exc
            element_id = region.element_id
        else:
            box = region.xyxy
            element_id = f"region-{index}"
        prompts.append(UISegmentationPrompt(element_id=element_id, box=tuple(box), points=[]))
    return prompts


def _filtered_selection(service, selection: UISelection, source_id: str, task_id: str, operation: str) -> ArtifactRef:
    return _rebind_selection_value(service, selection, source_id, task_id, operation)


def _artifact_key(ref: ArtifactRef) -> tuple[str, str, str]:
    return ref.task_id, ref.sha256, ref.role


def _is_artifact_mapping(value: Any) -> bool:
    return isinstance(value, Mapping) and {"task_id", "artifact_id", "sha256", "role"} <= set(value)


def _collect_refs(value: Any, result: dict[tuple[str, str, str], ArtifactRef]) -> None:
    if isinstance(value, Mapping):
        if _is_artifact_mapping(value):
            try:
                ref = ArtifactRef.model_validate(value)
            except Exception as exc:
                raise PipelineError("artifact_changed", "Nested layout artifact reference is invalid") from exc
            result[_artifact_key(ref)] = ref
            return
        for item in value.values():
            _collect_refs(item, result)
    elif isinstance(value, list):
        for item in value:
            _collect_refs(item, result)


def _rewrite_refs(value: Any, mapping: Mapping[tuple[str, str, str], ArtifactRef]) -> Any:
    if isinstance(value, Mapping):
        if _is_artifact_mapping(value):
            ref = ArtifactRef.model_validate(value)
            replacement = mapping.get(_artifact_key(ref))
            return replacement.model_dump(mode="json") if replacement is not None else dict(value)
        return {key: _rewrite_refs(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_refs(item, mapping) for item in value]
    return value


def _copy_nested_artifact(
    service,
    ref: ArtifactRef,
    task_id: str,
    operation: str,
    mapping: dict[tuple[str, str, str], ArtifactRef],
) -> ArtifactRef:
    key = _artifact_key(ref)
    if key in mapping:
        return mapping[key]
    raw = service.artifacts.read(ref)
    value = None
    if ref.media_type == "application/json":
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PipelineError("artifact_changed", "Nested layout artifact is not valid JSON") from exc
        nested: dict[tuple[str, str, str], ArtifactRef] = {}
        _collect_refs(value, nested)
        for nested_ref in nested.values():
            _copy_nested_artifact(service, nested_ref, task_id, operation, mapping)
        value = _rewrite_refs(value, mapping)
        if isinstance(value, dict) and value.get("task_id") == ref.task_id:
            value = {**value, "task_id": task_id}
        raw = canonical_json(value).encode()
    copied = service.artifacts.put(
        task_id,
        operation,
        raw,
        role=ref.role,
        media_type=ref.media_type,
        source_ids=[ref.artifact_id],
    )
    mapping[key] = copied
    return copied


def _rebind_selection_value(
    service,
    selection: UISelection,
    source_id: str,
    task_id: str,
    operation: str,
    *,
    reference_mapping: Mapping[Any, ArtifactRef] | None = None,
) -> ArtifactRef:
    source = next((x for x in selection.sources if x.source_id == source_id), None)
    if source is None:
        raise PipelineError("source_scope", "Selection does not include the requested source")
    mapping: dict[tuple[str, str, str], ArtifactRef] = {}
    for key, value in (reference_mapping or {}).items():
        if isinstance(key, tuple) and len(key) == 3:
            mapping[key] = value
        elif isinstance(key, ArtifactRef):
            mapping[_artifact_key(key)] = value
    if source.layout_ref is not None:
        layout = _copy_nested_artifact(service, source.layout_ref, task_id, operation, mapping)
        source = source.model_copy(update={"layout_ref": layout})
    value = UISelection(schema_version=1, sources=[source])
    return service.artifacts.put(
        task_id,
        operation,
        canonical_json(value).encode(),
        role="selection",
        source_ids=[source_id],
    )


def rebind_selection_for_child(
    service,
    selection_ref: ArtifactRef,
    child_task_id: str,
    *,
    source_id: str,
    reference_mapping: Mapping[Any, ArtifactRef] | None = None,
    operation: str = "input",
) -> ArtifactRef:
    """Copy one selected source and its trusted layout closure to a child.

    Geometry and element IDs are copied byte-for-byte.  Only artifact scope
    references and JSON ``task_id`` ownership fields are rewritten.  The
    helper is shared by planning and ``PipelineService.ui_child_plan`` so both
    sides derive the same child selection hash.
    """
    selection_ref = _ref(selection_ref, role="selection")
    selection = UISelection.model_validate_json(service.artifacts.read(selection_ref))
    return _rebind_selection_value(
        service,
        selection,
        source_id,
        child_task_id,
        operation,
        reference_mapping=reference_mapping,
    )


def _children(service, root_task_id: str) -> list[tuple[PipelinePlan, dict[str, Any]]]:
    with service.ledger.transaction() as db:
        rows = db.execute(
            "SELECT task_id, purpose, selection_hash, selection_revision, source_ids, status "
            "FROM ui_child_bindings WHERE root_task_id=? ORDER BY rowid",
            (root_task_id,),
        ).fetchall()
    return [(service.ledger.plan(row["task_id"]), dict(row)) for row in rows]


def _child_operation_state(service, child: PipelinePlan):
    operations = service.ledger.list_operations(child.task_id)
    if not operations:
        return "awaiting_approval", [], None
    active = [
        op for op in operations if op.state in {"prepared", "submitting", "submitted", "running", "outcome_unknown"}
    ]
    if active:
        if any(op.state == "outcome_unknown" for op in active):
            return "awaiting_reconciliation", [], "Child operation requires reconciliation"
        return "awaiting_approval", [], None
    succeeded = [op for op in operations if op.state == "succeeded"]
    if succeeded:
        refs = []
        for op in succeeded:
            for item in op.result.get("artifacts", []):
                refs.append(_ref(item))
        return "succeeded", refs, None
    return "failed", [], "Child operation failed"


def _release_confirmed(operation: Any) -> bool:
    """Accept only the service's structured resource-release proof.

    A bare boolean is deliberately rejected.  Backends may attach evidence
    such as the released device, so the proof is matched by its required
    ``released`` field rather than by exact dictionary equality.
    """
    proof = getattr(operation, "result", None)
    if not isinstance(proof, Mapping):
        return False
    release = proof.get("resource_release")
    return isinstance(release, Mapping) and release.get("released") is True


def _normalize_scoped_json(value: Any) -> Any:
    """Normalize child/root scope fields while preserving layout semantics."""
    if _is_artifact_mapping(value):
        # Child artifact ids/keys can differ even when the immutable payload is
        # equivalent.  Hash, role, and media type still identify the payload.
        return {
            "sha256": value.get("sha256"),
            "role": value.get("role"),
            "media_type": value.get("media_type"),
        }
    if isinstance(value, Mapping):
        return {
            key: ("<task-scope>" if key == "task_id" else _normalize_scoped_json(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_scoped_json(item) for item in value]
    return value


def _source_equivalent(service, left: UISelectionSource, right: UISelectionSource) -> bool:
    """Compare a root and child source without treating task rebinding as edit."""
    if left.model_dump(mode="json", exclude={"layout_ref"}) != right.model_dump(
        mode="json", exclude={"layout_ref"}
    ):
        return False
    if (left.layout_ref is None) != (right.layout_ref is None):
        return False
    if left.layout_ref is None:
        return True
    try:
        left_layout = _json(service.artifacts, left.layout_ref, label="Frozen layout")
        right_layout = _json(service.artifacts, right.layout_ref, label="Child layout")
    except PipelineError:
        return False
    return _normalize_scoped_json(left_layout) == _normalize_scoped_json(right_layout)


def _native_inpaint_contract() -> tuple[dict[str, str], Mapping[str, Any]]:
    """Read the committed recipe/contract/catalog and return file evidence.

    This is a planning read only.  It verifies that the native recipe still
    names the catalog model and that the synchronized UI/API/contract files
    exist, without probing or loading model weights.
    """
    try:
        from ..config import load_catalog, load_workflow_contract
        from ..paths import find_repo_root
        from ..workflows.compiler import load_recipe

        recipe = load_recipe("sdxl-inpaint")
        contract = load_workflow_contract("sdxl-inpaint")
        catalog = load_catalog()
        model_ids = catalog.by_id()
        if recipe.id != contract.id or recipe.models != contract.models or len(recipe.models) != 1:
            raise PipelineError("dependency_changed", "Native inpaint recipe and contract disagree")
        model = model_ids.get(recipe.models[0])
        if model is None or model.id != "sdxl-base-1.0" or not model.files:
            raise PipelineError("dependency_changed", "Native inpaint model is absent from the catalog")
        root = find_repo_root()
        relative = (
            "configs/workflows/recipes/sdxl-inpaint.yaml",
            "workflows/ui/sdxl-inpaint.json",
            "workflows/api/sdxl-inpaint.json",
            "workflows/contracts/sdxl-inpaint.yaml",
            "configs/models/catalog.yaml",
            "configs/runtime/comfyui.lock.yaml",
        )
        dependencies: dict[str, str] = {}
        for name in relative:
            path = root / name
            if not path.is_file():
                raise PipelineError("dependency_not_ready", f"Native inpaint dependency is missing: {name}")
            dependencies[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        metadata = {
            "recipe_id": recipe.id,
            "recipe_version": recipe.version,
            "model_id": model.id,
            "model_sha256": model.files[0].sha256,
            "mask_channel": "red",
            "mask_polarity": "white_is_edit",
            "mask_grow_by": 0,
        }
        return dependencies, metadata
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("dependency_not_ready", "Native inpaint contract is unavailable") from exc


class EditingExecution:
    """Deterministic coordinator for segmentation and masked generation."""

    def __init__(self, service):
        self.service = service
        self.store = service.artifacts

    def child_root(self, child: PipelinePlan | str) -> str:
        plan = _as_plan(self.service, child)
        try:
            with self.service.ledger.transaction() as db:
                row = db.execute(
                    "SELECT root_task_id FROM ui_child_bindings WHERE task_id=?", (plan.task_id,)
                ).fetchone()
        except Exception as exc:
            raise PipelineError("unknown_child") from exc
        if row is None:
            raise PipelineError("unknown_child")
        return row["root_task_id"]

    def binding(self, child: PipelinePlan | str, *, revision: int = 0, source_id: str | None = None) -> UIStepBinding:
        plan = _as_plan(self.service, child)
        from ..pipelines.ui_children import capability_for_plan, is_child_plan

        if not is_child_plan(plan):
            raise PipelineError("invalid_child")
        if type(revision) is not int or revision < 0:
            raise PipelineError("invalid_step")
        selection = _ref(plan.parameters.get("selection_ref"), role="selection")
        capability = capability_for_plan(plan)
        dependency_hashes: dict[str, str] = {}
        parameters_ref = None
        request_ref = _ref(plan.parameters.get("request_ref"), role="request")
        if capability == "ui.segment":
            try:
                request = UISegmentationRequest.model_validate_json(self.store.read(request_ref))
            except Exception as exc:
                raise PipelineError("artifact_changed", "Segmentation child request is unavailable") from exc
            dependency_hashes = {
                "sam-model": request.model_snapshot_ref.sha256,
                "sam-parameters": digest(
                    {
                        "prompt_version": request.prompt_version,
                        "prompts": [item.model_dump(mode="json") for item in request.prompts],
                    }
                ),
            }
        elif capability in {"ui.ocr", "ui.analyze"}:
            from .revision_inputs import LocalRevisionInputs, analysis_context

            request = LocalRevisionInputs.model_validate_json(self.store.read(request_ref))
            context = analysis_context(self.store, plan)
            parameters_ref = request.parameters_ref
            if capability == "ui.ocr":
                model = context.model_bindings.get("ocr")
                if model is None:
                    raise PipelineError("model_not_ready")
                dependency_hashes = {"ocr-model": model.sha256}
        return UIStepBinding(
            task_id=plan.task_id,
            step_id=capability.removeprefix("ui."),
            revision=revision,
            capability=capability,
            source_id=source_id,
            inputs=list(plan.inputs),
            selection_ref=selection,
            selection_revision=int(plan.parameters["selection_revision"]),
            selection_hash=selection.sha256,
            dependency_hashes=dependency_hashes,
            parameters_ref=parameters_ref,
        )

    prepare_binding = binding

    def validate_current(self, child: PipelinePlan | str) -> bool:
        """Verify that a child still names the current immutable selection.

        The ledger intentionally preserves old child plans.  This read-only
        check is used by the workflow immediately before approval/submission,
        including the interval before a newer selection is approved.
        """
        plan = _as_plan(self.service, child)
        root_id = self.child_root(plan)
        with self.service.ledger.transaction() as db:
            row = db.execute(
                "SELECT status,selection_hash,selection_revision,source_ids FROM ui_child_bindings WHERE task_id=?",
                (plan.task_id,),
            ).fetchone()
        if row is None:
            raise PipelineError("unknown_child")
        if row["status"] == "selection_superseded":
            raise PipelineError("selection_superseded")
        root = self.service.ledger.plan(root_id)
        request = UIAnalysisRequest.model_validate_json(
            self.store.read(_ref(root.parameters.get("request_ref"), role="request"))
        )
        current, _ = _selection_ref(self.service, root, request, None)
        if current is None:
            raise PipelineError("selection_superseded", "The child has no current selection")
        root_selection_value = plan.parameters.get("root_selection_ref")
        if root_selection_value is not None:
            expected_root = _ref(root_selection_value, role="selection")
            if expected_root.task_id != root_id or expected_root.sha256 != current.sha256:
                raise PipelineError("selection_superseded", "A newer selection replaced this child")
        elif row["selection_hash"] != current.sha256:
            # When rebinding changes the local hash, ui_child_plan records the
            # original root reference in plan.parameters.  This branch covers
            # the equal-bytes case and older child plans.
            raise PipelineError("selection_superseded", "A newer selection replaced this child")
        current_selection = UISelection.model_validate_json(self.store.read(current))
        selected_ids = set(json.loads(row["source_ids"]))
        child_selection = UISelection.model_validate_json(
            self.store.read(_ref(plan.parameters.get("selection_ref"), role="selection"))
        )
        current_sources = {x.source_id: x for x in current_selection.sources}
        if selected_ids != {x.source_id for x in child_selection.sources} or not selected_ids <= set(current_sources):
            raise PipelineError("selection_superseded", "A newer selection changed the child source scope")
        for item in child_selection.sources:
            if not _source_equivalent(self.service, item, current_sources[item.source_id]):
                raise PipelineError("selection_superseded", "A newer selection changed the child geometry")
        catalog = selection_tools.selection_catalog(self.service.artifacts, root_id)
        if catalog is not None and catalog.selection_ref is not None:
            if catalog.revision != int(row["selection_revision"]):
                raise PipelineError("selection_superseded", "A newer selection revision replaced this child")
        return True

    def _default_inpaint_plan(
        self,
        segment: PipelinePlan,
        root: PipelinePlan,
        request: UIAnalysisRequest,
        *,
        selection_ref: ArtifactRef,
        budget: TaskBudget | Mapping[str, Any] | None,
        segment_artifacts: Sequence[ArtifactRef] | None = None,
    ) -> EditingPreparation:
        """Materialize a deterministic image/mask bundle from SAM outputs."""
        # Once materialized, advance the immutable inpaint child instead of
        # recreating an approval gate after its result has been collected.
        for child, row in _children(self.service, root.task_id):
            if (
                row["purpose"] != "inpaint"
                or row["status"] == "selection_superseded"
                or child.parameters.get("parent_task_id") != segment.task_id
            ):
                continue
            self.validate_current(child)
            state, artifacts, reason = _child_operation_state(self.service, child)
            if state == "succeeded" and not any(
                _release_confirmed(op)
                for op in self.service.ledger.list_operations(child.task_id)
                if op.state == "succeeded"
            ):
                state = "awaiting_reconciliation"
                reason = "ComfyUI resource release has not been confirmed"
            return EditingPreparation(
                root_task_id=root.task_id,
                state=state,
                child=child,
                artifacts=artifacts,
                reason=reason,
                selection_ref=_ref(child.parameters["selection_ref"], role="selection"),
                selection_revision=int(child.parameters["selection_revision"]),
                source_id=json.loads(row["source_ids"])[0],
            )
        if segment_artifacts is None:
            operations = [op for op in self.service.ledger.list_operations(segment.task_id) if op.state == "succeeded"]
            available = [_ref(item) for op in operations for item in op.result.get("artifacts", [])]
        else:
            available = list(segment_artifacts)
        segment_output = next((ref for ref in available if ref.role == "segmentation"), None)
        if segment_output is None:
            raise PipelineError("dependency_not_ready", "Segmentation output is unavailable")
        bundle = _json(self.store, segment_output, label="Segmentation bundle")
        try:
            from ..vision.segmentation import SegmentationBundle

            parsed_bundle = SegmentationBundle.model_validate(bundle)
        except Exception as exc:
            raise PipelineError("artifact_changed", "Segmentation bundle is invalid") from exc
        if parsed_bundle.task_id != segment.task_id or parsed_bundle.canonical_ref.task_id != segment.task_id:
            raise PipelineError("artifact_scope", "Segmentation bundle is outside the child scope")
        if parsed_bundle.selection_hash != _ref(segment.parameters["selection_ref"], role="selection").sha256:
            raise PipelineError("selection_conflict", "Segmentation bundle selection changed")
        if parsed_bundle.status != "ready" or any(item.status != "ready" for item in parsed_bundle.items):
            raise PipelineError("dependency_not_ready", "Segmentation did not produce ready assets")
        item_values = [item.model_dump(mode="json") for item in parsed_bundle.items]
        local_selection = _ref(segment.parameters.get("selection_ref"), role="selection")
        selection = UISelection.model_validate_json(self.store.read(local_selection))
        if len(selection.sources) != 1:
            raise PipelineError("source_scope", "Final inpaint currently accepts one source")
        source_id = selection.sources[0].source_id
        canonical = _ref(segment.parameters.get("request_ref"), role="request")
        seg_request = UISegmentationRequest.model_validate_json(self.store.read(canonical))
        canonical_ref = seg_request.canonical_ref
        resources = seg_request.resources
        if canonical_ref.size_bytes > resources.max_image_bytes:
            raise PipelineError("input_limit", "Canonical image exceeds the approved image-size limit")
        canonical_bytes = self.store.read(canonical_ref)
        with Image.open(io.BytesIO(canonical_bytes)) as image:
            canonical_image = image.copy()
        if (
            canonical_image.width * canonical_image.height > resources.max_pixels
            or max(canonical_image.size) > resources.max_image_edge
        ):
            raise PipelineError("input_limit", "Canonical image exceeds the approved geometry limit")
        from .resources import check_storage

        check_storage(
            self.store,
            segment.task_id,
            resources,
            extra_bytes=canonical_ref.size_bytes
            + len(item_values) * canonical_image.width * canonical_image.height * 6,
            memory_bytes=canonical_image.width * canonical_image.height * 20,
        )
        masks: dict[str, Image.Image] = {}
        for item in item_values:
            if not isinstance(item, Mapping):
                continue
            element_id = item.get("element_id")
            mask_value = item.get("contour_mask_ref") or item.get("estimated_alpha_ref")
            if not isinstance(element_id, str) or mask_value is None:
                continue
            mask_ref = _ref(mask_value)
            with Image.open(io.BytesIO(self.store.read(mask_ref))) as mask_image:
                masks[element_id] = mask_image.convert("L")
        source_model = selection.sources[0]
        mask_result = reconstruct_edit_mask(
            selection,
            source_id,
            size=canonical_image.size,
            segmentation_masks=masks,
            element_boxes=_layout_boxes(self.store, source_model),
        )
        if mask_result.status == "no_edit_pixels":
            unchanged = self.store.put(
                root.task_id, "reconstruction", canonical_bytes,
                role="reconstruction", media_type=canonical_ref.media_type,
                source_ids=[canonical_ref.artifact_id, segment_output.artifact_id],
            )
            return EditingPreparation(
                root_task_id=root.task_id,
                state="succeeded",
                artifacts=[segment_output, unchanged],
                reason="no_edit_pixels",
                selection_ref=local_selection,
                selection_revision=int(segment.parameters["selection_revision"]),
                source_id=source_id,
            )
        crop = mask_result.mask.getbbox()
        if crop is None:
            raise PipelineError("no_edit_pixels", "The final edit mask is empty")
        crop_x1, crop_y1, crop_x2, crop_y2 = crop
        crop_width, crop_height = crop_x2 - crop_x1, crop_y2 - crop_y1
        scale = min(1.0, 1024 / max(crop_width, crop_height))
        resize = (
            max(8, min(1024, math.ceil(crop_width * scale / 8) * 8)),
            max(8, min(1024, math.ceil(crop_height * scale / 8) * 8)),
        )
        from .inpaint import prepare_edit_mask, resize_and_pad_image

        canonical_crop = canonical_image.crop(crop)
        prepared_image = resize_and_pad_image(canonical_crop, resize=resize, pad=(0, 0, 0, 0))
        prepared_mask = prepare_edit_mask(
            mask_result.mask.crop(crop),
            channel="red",
            polarity="white_is_edit",
            resize=resize,
            pad=(0, 0, 0, 0),
        )
        child_id = (
            "ui-inpaint-"
            + digest(
                {
                    "segment": segment.fingerprint,
                    "mask": hashlib.sha256(mask_result.mask.tobytes()).hexdigest(),
                    "selection": selection_ref.sha256,
                }
            )[:40]
        )
        mask_bytes = _png(prepared_mask)
        canonical_mask_bytes = _png(mask_result.mask)
        image_bytes = canonical_bytes
        prepared_image_bytes = _png(prepared_image)
        canonical_child = self.store.put(
            child_id,
            "input",
            image_bytes,
            role="canonical",
            media_type="image/png",
            source_ids=[canonical_ref.artifact_id],
        )
        image_child = self.store.put(
            child_id,
            "input",
            prepared_image_bytes,
            role="image",
            media_type="image/png",
            source_ids=[canonical_ref.artifact_id],
        )
        mask_child = self.store.put(
            child_id,
            "input",
            mask_bytes,
            role="edit_mask",
            media_type="image/png",
            source_ids=[canonical_ref.artifact_id],
        )
        canonical_mask_child = self.store.put(
            child_id,
            "input",
            canonical_mask_bytes,
            role="edit_mask",
            media_type="image/png",
            source_ids=[canonical_ref.artifact_id],
        )
        transform_child = self.store.put(
            child_id,
            "input",
            canonical_json(
                {
                    "schema_version": 1,
                    "canonical_size": [canonical_image.width, canonical_image.height],
                    "model_size": [resize[0], resize[1]],
                    "crop": [crop_x1, crop_y1, crop_x2, crop_y2],
                    "resize": [resize[0], resize[1]],
                    "pad": [0, 0, 0, 0],
                    "inverse_scale": [crop_width / resize[0], crop_height / resize[1]],
                    "inverse": [
                        [crop_width / resize[0], 0.0, crop_x1],
                        [0.0, crop_height / resize[1], crop_y1],
                        [0.0, 0.0, 1.0],
                    ],
                }
            ).encode(),
            role="view_transform",
            source_ids=[canonical_ref.artifact_id],
        )
        local_selection_for_child = _rebind_selection_value(
            self.service,
            selection,
            source_id,
            child_id,
            "input",
            reference_mapping={_artifact_key(canonical_ref): canonical_child},
        )
        child_budget = _budget(budget, root.envelope.budget)
        native_dependencies, _native_metadata = _native_inpaint_contract()
        target_prompt = (
            "restore the selected map surface"
            if request.reconstruction_target == "map_surface"
            else "restore the selected scene background"
        )
        envelope = ExecutionEnvelope(
            backend="comfy",
            model="sdxl-base-1.0",
            recipe="sdxl-inpaint",
            max_width=1024,
            max_height=1024,
            allowed_tools=["generate"],
            mutable_parameters=["negative_prompt", "prompt", "seed"],
            budget=child_budget,
        )
        from ..schemas.agent import ImageMaskBinding

        binding = ImageMaskBinding(
            image_ref=image_child,
            mask_ref=mask_child,
            canonical_ref=canonical_child,
            canonical_edit_mask_ref=canonical_mask_child,
            selection_ref=local_selection_for_child,
            view_transform_ref=transform_child,
            selection_revision=int(segment.parameters["selection_revision"]),
            selection_hash=local_selection_for_child.sha256,
            width=resize[0],
            height=resize[1],
            canonical_width=canonical_image.width,
            canonical_height=canonical_image.height,
            crop=(crop_x1, crop_y1, crop_x2, crop_y2),
            resize=resize,
            pad=(0, 0, 0, 0),
            mask_policy_version="canonical-edit-v1",
        )
        masked = MaskedGenerationPlan(
            schema_version=2,
            task_id=child_id,
            session_id=root.task_id,
            intent=GenerationIntent.image_to_image,
            user_intent=target_prompt,
            backend="comfy",
            model="sdxl-base-1.0",
            recipe="sdxl-inpaint",
            dependency_hashes=native_dependencies,
            parameters={
                "prompt": target_prompt,
                "negative_prompt": "text, watermark, blurry",
                "seed": 20260831,
                "steps": 25,
                "cfg": 6.5,
                "denoise": 0.6,
            },
            acceptance_criteria=["Keep pixels outside the approved edit mask unchanged"],
            envelope=envelope,
            image_mask=binding,
        )
        request_ref = self.store.put(child_id, "request", canonical_json(masked).encode(), role="request")
        child = self.service.ui_child_plan(
            child_id,
            parent_task_id=segment.task_id,
            purpose="inpaint",
            source_ids=[source_id],
            request_ref=request_ref,
            selection_ref=selection_ref,
            selection_revision=int(segment.parameters["selection_revision"]),
            budget=child_budget,
        )
        return EditingPreparation(
            root_task_id=root.task_id,
            state="awaiting_approval",
            child=child,
            artifacts=[
                request_ref,
                image_child,
                mask_child,
                canonical_child,
                canonical_mask_child,
                local_selection_for_child,
                transform_child,
            ],
            selection_ref=_ref(child.parameters["selection_ref"], role="selection"),
            selection_revision=int(segment.parameters["selection_revision"]),
            source_id=source_id,
            reason=mask_result.reason,
        )

    def status(self, root: PipelinePlan | str) -> EditingPreparation:
        plan = _as_plan(self.service, root)
        for child, row in reversed(_children(self.service, plan.task_id)):
            state, artifacts, reason = _child_operation_state(self.service, child)
            if row["status"] == "selection_superseded":
                continue
            return EditingPreparation(
                root_task_id=plan.task_id,
                state=state,
                child=child,
                artifacts=artifacts,
                reason=reason,
                selection_ref=_ref(child.parameters["selection_ref"], role="selection"),
                selection_revision=int(child.parameters["selection_revision"]),
                source_id=(json.loads(row["source_ids"])[0] if row.get("source_ids") else None),
            )
        return EditingPreparation(
            root_task_id=plan.task_id, state="awaiting_selection", reason="No editing child is planned"
        )

    def _propose_from_frozen_layout(self, root: PipelinePlan, request: UIAnalysisRequest) -> bool:
        """Create the bounded static proposal for a completed analysis root.

        ``UIAnalyzer.propose_selection`` only projects an already frozen layout;
        it does not invoke the VLM or any other provider.  This keeps a fresh
        completed analysis on the same path as a reviewed or automatic input.
        """
        catalog = selection_tools.selection_catalog(self.service.artifacts, root.task_id)
        if catalog is not None:
            return False
        layout_ref = None
        for name in ("layout_ref", "review_layout_ref"):
            value = request.model_bindings.get(name)
            if value is None:
                continue
            candidate = _ref(value)
            if candidate.role in {"layout", "review_layout"}:
                layout_ref = candidate
                break
        canonical_ref = _model_binding_ref(request, "canonical_ref", role="canonical")
        outputs = _successful_outputs(self.service, root.task_id)
        if canonical_ref is None:
            canonical_values = [item for item in outputs if item.role == "canonical"]
            if len(canonical_values) == 1:
                canonical_ref = canonical_values[0]
        if layout_ref is None:
            layouts = [item for item in outputs if item.role in {"layout", "review_layout"}]
            if len(layouts) == 1:
                layout_ref = layouts[0]
        if layout_ref is None or canonical_ref is None:
            return False
        try:
            layout = _json(self.store, layout_ref, label="Frozen layout")
            source_id = layout.get("source_id") if isinstance(layout, Mapping) else None
            originals = _root_originals(self.service, root, request)
            if not isinstance(source_id, str):
                if len(originals) != 1:
                    return False
                source_id = selection_tools.source_id_for_ref(originals[0], 1)
            original = next(
                (item for item in originals if item.sha256 == canonical_ref.sha256),
                originals[0] if len(originals) == 1 else None,
            )
            if original is None:
                return False
            trusted = selection_tools.TrustedSource(
                source_id,
                original,
                width=layout.get("width") if isinstance(layout, Mapping) else None,
                height=layout.get("height") if isinstance(layout, Mapping) else None,
                layout_ref=layout_ref,
                canonical_ref=canonical_ref,
            )
            from ..agent.ui_analyzer import UIAnalyzer

            UIAnalyzer.propose_selection(
                self.store,
                root.task_id,
                trusted,
                layout_ref,
                output_mode=request.output_mode,
                target=request.reconstruction_target,
                remove_text=request.remove_text,
            )
            return True
        except PipelineError:
            raise
        except (KeyError, TypeError, ValueError):
            return False

    def prepare(
        self,
        root: PipelinePlan | str,
        *,
        selection_ref: ArtifactRef | None = None,
        selection_revision: int | None = None,
        model_snapshot_ref: ArtifactRef | None = None,
        canonical_ref: ArtifactRef | None = None,
        canonical_refs: Mapping[str, ArtifactRef] | None = None,
        prompts: Sequence[UISegmentationPrompt | Mapping[str, Any]] | None = None,
        budget: TaskBudget | Mapping[str, Any] | None = None,
        source_id: str | None = None,
    ) -> EditingPreparation:
        """Advance one editing child using frozen inputs and offline locks.

        Freezing a SAM lock here never marks its runtime or model files ready;
        execution validates those resources separately before submission.
        """
        root_plan = _as_plan(self.service, root)
        if root_plan.workflow_type != "ui_analysis":
            raise PipelineError("invalid_plan", "Editing roots must be UI analysis plans")
        request_ref = _ref(root_plan.parameters.get("request_ref"), role="request")
        request = UIAnalysisRequest.model_validate_json(self.store.read(request_ref))
        if request.output_mode == "parse":
            raise PipelineError("invalid_plan", "Parse plans cannot enter the editing workflow")
        frozen_selection, candidate_artifacts = _selection_ref(self.service, root_plan, request, selection_ref)
        if frozen_selection is None:
            self._propose_from_frozen_layout(root_plan, request)
            frozen_selection, candidate_artifacts = _selection_ref(self.service, root_plan, request, selection_ref)
        if frozen_selection is None:
            return EditingPreparation(
                root_task_id=root_plan.task_id,
                state="awaiting_selection",
                artifacts=candidate_artifacts,
                reason="Selection is not recorded",
            )
        selection = UISelection.model_validate_json(self.store.read(frozen_selection))
        sources = _sources(self.service, root_plan, request, selection)
        chosen = [source_id] if source_id is not None else [item.source_id for item in selection.sources]
        if source_id is not None and source_id not in sources:
            raise PipelineError("source_scope", "Requested editing source is not selected")
        chosen = [item for item in chosen if item in sources]
        if not chosen:
            raise PipelineError("source_scope", "Selection has no usable source")
        # Root orchestration deliberately advances one source per preparation
        # call; this keeps provider submissions serial and retryable.
        selected_source_id = chosen[0]
        revision = (
            selection_revision
            if selection_revision is not None
            else _selection_revision(self.service, root_plan, frozen_selection)
        )
        if type(revision) is not int or revision < 0:
            raise PipelineError("invalid_selection", "Selection revision is invalid")
        for child, row in _children(self.service, root_plan.task_id):
            child_root_ref = child.parameters.get("root_selection_ref")
            child_root_hash = (
                _ref(child_root_ref, role="selection").sha256
                if child_root_ref is not None
                else row["selection_hash"]
            )
            same = (
                row["purpose"] == "segmentation"
                and child_root_hash == frozen_selection.sha256
                and int(row["selection_revision"]) == revision
                and selected_source_id in json.loads(row["source_ids"])
            )
            if same:
                state, artifacts, reason = _child_operation_state(self.service, child)
                if state == "succeeded" and request.output_mode == "decompose" and row["purpose"] == "segmentation":
                    if not any(
                        _release_confirmed(op)
                        for op in self.service.ledger.list_operations(child.task_id)
                        if op.state == "succeeded"
                    ):
                        return EditingPreparation(
                            root_task_id=root_plan.task_id,
                            state="awaiting_reconciliation",
                            child=child,
                            artifacts=artifacts,
                            reason="SAM resource release has not been confirmed",
                            selection_ref=_ref(child.parameters["selection_ref"], role="selection"),
                            selection_revision=revision,
                            source_id=selected_source_id,
                        )
                    from .editing_outputs import finalize_decomposition

                    final_artifacts = finalize_decomposition(self.service, root_plan, child, artifacts)
                    return EditingPreparation(
                        root_task_id=root_plan.task_id,
                        state="succeeded",
                        child=child,
                        artifacts=list(final_artifacts),
                        selection_ref=_ref(child.parameters["selection_ref"], role="selection"),
                        selection_revision=revision,
                        source_id=selected_source_id,
                    )
                if state == "succeeded" and request.output_mode == "reconstruct" and row["purpose"] == "segmentation":
                    release_confirmed = any(
                        _release_confirmed(op)
                        for op in self.service.ledger.list_operations(child.task_id)
                        if op.state == "succeeded"
                    )
                    if not release_confirmed:
                        return EditingPreparation(
                            root_task_id=root_plan.task_id,
                            state="awaiting_reconciliation",
                            child=child,
                            artifacts=artifacts,
                            reason="SAM resource release has not been confirmed",
                            selection_ref=_ref(child.parameters["selection_ref"], role="selection"),
                            selection_revision=revision,
                            source_id=selected_source_id,
                        )
                    # The concrete final image/mask is derived only after the
                    # segmentation output and release proof are durable.
                    return self._default_inpaint_plan(
                        child,
                        root_plan,
                        request,
                        selection_ref=frozen_selection,
                        budget=budget,
                    )
                return EditingPreparation(
                    root_task_id=root_plan.task_id,
                    state=state,
                    child=child,
                    artifacts=artifacts,
                    reason=reason,
                    selection_ref=_ref(child.parameters["selection_ref"], role="selection"),
                    selection_revision=revision,
                    source_id=selected_source_id,
                )
            if row["status"] in {"pending", "active"}:
                states = {op.state for op in self.service.ledger.list_operations(child.task_id)}
                if states & {"submitting", "submitted", "running", "outcome_unknown"}:
                    return EditingPreparation(
                        root_task_id=root_plan.task_id,
                        state="awaiting_reconciliation",
                        reason="An earlier editing child is still in flight",
                    )
        snapshot = model_snapshot_ref
        if snapshot is None:
            # Prefer a caller-frozen snapshot; otherwise read only the local
            # platform lock. No model loading or download occurs in planning.
            snapshot = request.model_bindings.get("sam")
        if snapshot is None:
            snapshot = _frozen_sam_snapshot(self.service, root_plan)
        snapshot = _ref(snapshot, role="model_snapshot")
        if snapshot.task_id != root_plan.task_id:
            raise PipelineError("artifact_scope", "SAM model snapshot must be frozen in the root scope")
        bound_canonical = canonical_ref
        if bound_canonical is None and canonical_refs is None:
            bound_canonical = _model_binding_ref(request, "canonical_ref", role="canonical")
        canonical_map = _canonical_by_source(
            self.service,
            root_plan,
            [selected_source_id],
            _successful_outputs(self.service, root_plan.task_id),
            {"canonical_ref": bound_canonical, "canonical_refs": canonical_refs},
        )
        canonical = canonical_map.get(selected_source_id)
        if canonical is None and len(canonical_map) == 1:
            canonical = next(iter(canonical_map.values()))
        if canonical is None:
            raise PipelineError("dependency_not_ready", "Canonical output is not ready for this source")
        child_id = (
            "ui-seg-"
            + digest(
                {
                    "root": root_plan.task_id,
                    "source": selected_source_id,
                    "selection": frozen_selection.sha256,
                    "revision": revision,
                    "canonical": canonical.sha256,
                    "snapshot": snapshot.sha256,
                }
            )[:40]
        )
        child_canonical = self.store.put(
            child_id,
            "input",
            self.store.read(canonical),
            role="canonical",
            media_type=canonical.media_type,
            source_ids=[canonical.artifact_id],
        )
        local_selection = _rebind_selection_value(
            self.service,
            selection,
            selected_source_id,
            child_id,
            "input",
            reference_mapping={_artifact_key(canonical): child_canonical},
        )
        child_snapshot = self.store.put(
            child_id,
            "model",
            self.store.read(snapshot),
            role="model_snapshot",
            media_type=snapshot.media_type,
            source_ids=[snapshot.artifact_id],
        )
        prompts_value = _prompts(
            self.store,
            selection.sources[0]
            if selected_source_id == selection.sources[0].source_id
            else next(x for x in selection.sources if x.source_id == selected_source_id),
            prompts,
        )
        request_value = UISegmentationRequest(
            canonical_ref=child_canonical,
            selection_ref=local_selection,
            selection_revision=revision,
            selection_hash=local_selection.sha256,
            prompts=prompts_value,
            model_snapshot_ref=child_snapshot,
            prompt_version="selection-v1",
            resources=request.resources,
            result_roles=["segmentation"],
        )
        child_request = self.store.put(child_id, "request", canonical_json(request_value).encode(), role="request")
        child_budget = _budget(budget, root_plan.envelope.budget)
        child = self.service.ui_child_plan(
            child_id,
            parent_task_id=root_plan.task_id,
            purpose="segmentation",
            source_ids=[selected_source_id],
            request_ref=child_request,
            selection_ref=frozen_selection,
            selection_revision=revision,
            budget=child_budget,
        )
        return EditingPreparation(
            root_task_id=root_plan.task_id,
            state="awaiting_approval",
            child=child,
            artifacts=[local_selection, child_canonical, child_snapshot, child_request],
            selection_ref=_ref(child.parameters["selection_ref"], role="selection"),
            selection_revision=revision,
            source_id=selected_source_id,
        )

    def plan_inpaint(
        self,
        segmentation: PipelinePlan | str,
        masked_plan: MaskedGenerationPlan | Mapping[str, Any] | ArtifactRef,
        *,
        selection_ref: ArtifactRef | None = None,
        budget: TaskBudget | Mapping[str, Any] | None = None,
    ) -> EditingPreparation:
        """Register a final masked-generation child after SAM release.

        ``masked_plan`` is an actual v2 plan (or a task-scoped artifact
        containing one); this method never invents model, recipe, image, or
        mask references.
        """
        segment = _as_plan(self.service, segmentation)
        if segment.workflow_type != "ui_segmentation":
            raise PipelineError("invalid_child")
        root_id = self.child_root(segment)
        root = self.service.ledger.plan(root_id)
        operations = self.service.ledger.list_operations(segment.task_id)
        succeeded = [op for op in operations if op.state == "succeeded"]
        if not succeeded:
            if any(op.state == "outcome_unknown" for op in operations):
                raise PipelineError("awaiting_reconciliation")
            raise PipelineError("dependency_not_ready", "Segmentation has not succeeded")
        if not any(_release_confirmed(op) for op in succeeded):
            raise PipelineError("resource_busy", "SAM resource release has not been confirmed")
        if isinstance(masked_plan, ArtifactRef):
            source_plan_ref = _ref(masked_plan, role="request")
            raw = _json(self.store, source_plan_ref, label="Masked generation plan")
            try:
                parsed = MaskedGenerationPlan.model_validate(raw)
            except Exception as exc:
                raise PipelineError("invalid_plan", "Masked generation plan is invalid") from exc
        else:
            try:
                parsed = (
                    masked_plan
                    if isinstance(masked_plan, MaskedGenerationPlan)
                    else MaskedGenerationPlan.model_validate(masked_plan)
                )
            except Exception as exc:
                raise PipelineError("invalid_plan", "Masked generation plan is invalid") from exc
        root_request = UIAnalysisRequest.model_validate_json(
            self.store.read(_ref(root.parameters.get("request_ref"), role="request"))
        )
        chosen_selection, _ = _selection_ref(self.service, root, root_request, selection_ref)
        if chosen_selection is None:
            raise PipelineError("selection_superseded", "The root has no current selection")
        self.store.read(chosen_selection)
        selection = UISelection.model_validate_json(self.store.read(chosen_selection))
        sources = [x.source_id for x in selection.sources]
        if len(sources) != 1:
            raise PipelineError("source_scope", "Inpaint currently accepts one selected source")
        source_ids = sources
        child_id = (
            "ui-inpaint-"
            + digest(
                {
                    "root": root_id,
                    "segment": segment.fingerprint,
                    "plan": parsed.model_dump(mode="json"),
                    "selection": chosen_selection.sha256,
                }
            )[:40]
        )
        mapping: dict[tuple[str, str, str], ArtifactRef] = {}
        for field in ("image_ref", "mask_ref", "canonical_ref", "canonical_edit_mask_ref", "view_transform_ref"):
            old = getattr(parsed.image_mask, field)
            self.store.read(old)
            mapping[_artifact_key(old)] = self.store.put(
                child_id,
                "input",
                self.store.read(old),
                role=old.role,
                media_type=old.media_type,
                source_ids=[old.artifact_id],
            )
        # A root selection's layout may still point at the segmentation
        # canonical.  Reuse the exact copied canonical for that nested ref so
        # the trusted-source binding and ImageMaskBinding agree byte-for-byte.
        canonical_child = mapping[_artifact_key(parsed.image_mask.canonical_ref)]
        for source in selection.sources:
            if source.layout_ref is None:
                continue
            layout = _json(self.store, source.layout_ref, label="Frozen layout")
            nested: dict[tuple[str, str, str], ArtifactRef] = {}
            _collect_refs(layout, nested)
            for nested_ref in nested.values():
                if nested_ref.role == "canonical" and nested_ref.sha256 == parsed.image_mask.canonical_ref.sha256:
                    mapping[_artifact_key(nested_ref)] = canonical_child
        local_selection = _rebind_selection_value(
            self.service,
            selection,
            source_ids[0],
            child_id,
            "input",
            reference_mapping=mapping,
        )
        binding = parsed.image_mask.model_copy(
            update={
                **{
                    field: mapping[_artifact_key(getattr(parsed.image_mask, field))]
                    for field in (
                        "image_ref",
                        "mask_ref",
                        "canonical_ref",
                        "canonical_edit_mask_ref",
                        "view_transform_ref",
                    )
                },
                "selection_ref": local_selection,
                "selection_hash": local_selection.sha256,
            }
        )
        child_budget = _budget(budget, parsed.envelope.budget)
        if child_budget != parsed.envelope.budget:
            parsed = parsed.model_copy(update={"envelope": parsed.envelope.model_copy(update={"budget": child_budget})})
        child_payload = parsed.model_copy(update={"task_id": child_id, "image_mask": binding})
        request_ref = self.store.put(child_id, "request", canonical_json(child_payload).encode(), role="request")
        child = self.service.ui_child_plan(
            child_id,
            parent_task_id=segment.task_id,
            purpose="inpaint",
            source_ids=source_ids,
            request_ref=request_ref,
            selection_ref=chosen_selection,
            selection_revision=binding.selection_revision,
            budget=child_budget,
        )
        return EditingPreparation(
            root_task_id=root_id,
            state="awaiting_approval",
            child=child,
            artifacts=[local_selection, request_ref, *mapping.values()],
            selection_ref=_ref(child.parameters["selection_ref"], role="selection"),
            selection_revision=binding.selection_revision,
            source_id=source_ids[0],
        )


def reconstruct_edit_mask(
    selection: UISelection | Mapping[str, Any],
    source_id: str,
    *,
    size: tuple[int, int],
    segmentation_masks: Mapping[str, Image.Image] | None = None,
    element_boxes: Mapping[str, tuple[int, int, int, int]] | None = None,
) -> EditMaskResult:
    """Build a canonical binary edit mask from selection and optional masks.

    Target regions contribute edit pixels.  Keep elements are cleared after
    the target union; remove elements remain editable.  The function is pure
    CPU work and intentionally returns ``no_edit_pixels`` for an empty final
    mask so callers do not submit a meaningless generation child.
    """
    parsed = selection if isinstance(selection, UISelection) else UISelection.model_validate(selection)
    source = next((x for x in parsed.sources if x.source_id == source_id), None)
    if source is None:
        raise PipelineError("source_scope", "Selection source is not present")
    width, height = size
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError("size must contain positive integers")
    canvas = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(canvas)
    for region in source.target_regions:
        if region.kind == "bbox":
            x1, y1, x2, y2 = region.xyxy
            draw.rectangle((x1, y1, x2 - 1, y2 - 1), fill=255)
        elif region.element_id in (segmentation_masks or {}):
            mask = (segmentation_masks or {})[region.element_id]
            if not isinstance(mask, Image.Image) or mask.size != (width, height):
                raise ValueError("Segmentation masks must match canonical size")
            constrained = mask.convert("L")
            box = (element_boxes or {}).get(region.element_id)
            if box is not None:
                roi = Image.new("L", (width, height), 0)
                ImageDraw.Draw(roi).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=255)
                constrained = ImageChops.multiply(constrained, roi)
            canvas = ImageChops.lighter(canvas, constrained)
        elif region.element_id in (element_boxes or {}):
            x1, y1, x2, y2 = (element_boxes or {})[region.element_id]
            ImageDraw.Draw(canvas).rectangle((x1, y1, x2 - 1, y2 - 1), fill=255)
    if segmentation_masks:
        # Segment masks for explicitly selected elements can refine the union
        # but never enlarge a target outside its existing scope.
        for element_id in source.keep_elements:
            mask = segmentation_masks.get(element_id)
            if mask is not None:
                canvas = ImageChops.subtract(canvas, mask.convert("L"))
    if source.keep_elements and element_boxes:
        keep = Image.new("L", (width, height), 0)
        keep_draw = ImageDraw.Draw(keep)
        for element_id in source.keep_elements:
            box = element_boxes.get(element_id)
            if box is not None:
                keep_draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=255)
        canvas = ImageChops.subtract(canvas, keep)
    if canvas.getbbox() is None:
        return EditMaskResult(canvas, "no_edit_pixels", "The final edit mask is empty")
    return EditMaskResult(canvas, "ready")


__all__ = [
    "EditMaskResult",
    "EditingExecution",
    "EditingPreparation",
    "rebind_selection_for_child",
    "reconstruct_edit_mask",
]
