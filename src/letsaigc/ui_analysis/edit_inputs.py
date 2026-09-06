"""Read-only rebinding of a completed automatic UI parse.

This module copies immutable parse evidence into a new task scope.  It never
opens the human review repository and never registers ledger operations.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from ..execution.temporal.projection import local_projection
from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, PipelineRun, canonical_json
from ..schemas.ui import CanonicalImage
from ..schemas.ui_provider import ManualUIInput, UIInputEntry, UIInputManifest, UIProvisionResult, UISource
from .execution import UIExecution


def _unavailable(message: str = "Frozen automatic UI input is unavailable") -> PipelineError:
    return PipelineError("automatic_input_unavailable", message)


def _json(store, ref: ArtifactRef, message: str) -> Any:
    try:
        return json.loads(store.read(ref))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise PipelineError("artifact_changed", message) from exc


def _ref_key(ref: ArtifactRef) -> tuple[str, str, str]:
    return ref.task_id, ref.sha256, ref.role


def _is_ref(value: Any) -> bool:
    return isinstance(value, Mapping) and {"task_id", "artifact_id", "sha256", "role"} <= set(value)


def _collect_refs(value: Any, result: dict[tuple[str, str, str], ArtifactRef]) -> None:
    if isinstance(value, Mapping):
        if _is_ref(value):
            try:
                ref = ArtifactRef.model_validate(value)
            except Exception as exc:
                raise PipelineError("artifact_changed", "Automatic evidence contains an invalid reference") from exc
            result[_ref_key(ref)] = ref
            return
        for child in value.values():
            _collect_refs(child, result)
    elif isinstance(value, list):
        for child in value:
            _collect_refs(child, result)


def _rewrite_refs(value: Any, mapping: Mapping[tuple[str, str, str], ArtifactRef]) -> Any:
    if isinstance(value, Mapping):
        if _is_ref(value):
            try:
                ref = ArtifactRef.model_validate(value)
            except Exception as exc:
                raise PipelineError("artifact_changed", "Automatic evidence contains an invalid reference") from exc
            replacement = mapping.get(_ref_key(ref))
            if replacement is not None:
                return replacement.model_dump(mode="json")
        return {key: _rewrite_refs(child, mapping) for key, child in value.items()}
    if isinstance(value, list):
        return [_rewrite_refs(child, mapping) for child in value]
    return value


def _one(refs: list[ArtifactRef], role: str) -> ArtifactRef:
    found = [ref for ref in refs if ref.role == role]
    if len(found) != 1:
        raise _unavailable(f"Frozen automatic parse must contain one {role} artifact")
    return found[0]


def _integer_coordinate(value: Any) -> bool:
    """Accept producer JSON's integral floats without accepting fractional boxes."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and float(value).is_integer()


def _validate_layout(
    store, layout_ref: ArtifactRef, canonical: CanonicalImage, texts_ref: ArtifactRef
) -> dict[str, Any]:
    layout = _json(store, layout_ref, "Automatic layout is not valid JSON")
    if not isinstance(layout, dict) or layout.get("schema_version") != 1:
        raise _unavailable("Automatic layout schema is unsupported")
    if layout.get("source_id") != canonical.source_id:
        raise PipelineError("artifact_changed", "Automatic layout source identity changed")
    try:
        layout_canonical = ArtifactRef.model_validate(layout.get("canonical_ref"))
    except Exception as exc:
        raise PipelineError("artifact_changed", "Automatic layout canonical reference is invalid") from exc
    if (
        _ref_key(layout_canonical) != _ref_key(canonical.canonical_ref)
        or layout.get("canonical_sha256") != canonical.canonical_ref.sha256
        or layout.get("width") != canonical.width
        or layout.get("height") != canonical.height
    ):
        raise PipelineError("artifact_changed", "Automatic layout canonical identity changed")
    embedded_texts = layout.get("texts_ref")
    if embedded_texts is not None:
        try:
            if _ref_key(ArtifactRef.model_validate(embedded_texts)) != _ref_key(texts_ref):
                raise PipelineError("artifact_changed", "Automatic layout text reference changed")
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError("artifact_changed", "Automatic layout text reference is invalid") from exc
    elements = layout.get("elements")
    if not isinstance(elements, list):
        raise PipelineError("artifact_changed", "Automatic layout elements are invalid")
    seen: set[str] = set()
    for element in elements:
        if not isinstance(element, dict) or not isinstance(element.get("element_id"), str):
            raise PipelineError("artifact_changed", "Automatic layout element identity is invalid")
        element_id = element["element_id"]
        if element_id in seen:
            raise PipelineError("artifact_changed", "Automatic layout element identity is duplicated")
        seen.add(element_id)
        box = element.get("bbox")
        if (
            not isinstance(box, list)
            or len(box) != 4
            or any(not _integer_coordinate(value) for value in box)
            or not 0 <= box[0] < box[2] <= canonical.width
            or not 0 <= box[1] < box[3] <= canonical.height
        ):
            raise PipelineError("artifact_changed", "Automatic layout bounding box is invalid")
    return layout


def _validate_texts(store, texts_ref: ArtifactRef, canonical: CanonicalImage) -> dict[str, Any]:
    texts = _json(store, texts_ref, "Automatic texts are not valid JSON")
    if not isinstance(texts, dict) or texts.get("schema_version") != 1:
        raise _unavailable("Automatic texts schema is unsupported")
    if texts.get("canonical_sha256") != canonical.canonical_ref.sha256:
        raise PipelineError("artifact_changed", "Automatic text canonical identity changed")
    if not isinstance(texts.get("texts"), list):
        raise PipelineError("artifact_changed", "Automatic text regions are invalid")
    return texts


def _source_refs(source: UISource, source_result_ref: ArtifactRef) -> dict[str, dict[str, Any]]:
    return {
        "sources_ref": source_result_ref.model_dump(mode="json"),
        "original_ref": source.original_ref.model_dump(mode="json"),
        "provenance_ref": source.provenance_ref.model_dump(mode="json"),
    }


def automatic_input(
    service, new_task_id: str, source_task_id: str
) -> tuple[ManualUIInput, dict[str, Any], dict[str, ArtifactRef]]:
    """Freeze a succeeded automatic parse for a new offline editing plan."""

    if new_task_id == source_task_id:
        raise PipelineError("artifact_scope", "Automatic input requires a new task scope")
    store = service.artifacts
    try:
        plan = service.ledger.plan(source_task_id)
    except PipelineError as exc:
        raise _unavailable() from exc
    if plan.workflow_type != "ui_analysis":
        raise _unavailable("Source task is not a UI analysis task")
    execution = UIExecution(service)
    try:
        request = execution.request(plan)
    except Exception as exc:
        raise _unavailable("Source UI request is unavailable") from exc
    if request.output_mode != "parse":
        raise _unavailable("Automatic rebinding accepts parse tasks only")
    raw_run = local_projection(service, source_task_id)
    try:
        run = PipelineRun.model_validate(raw_run) if raw_run is not None else None
    except Exception as exc:
        raise _unavailable("Source execution projection is invalid") from exc
    if (
        run is None
        or run.task_id != source_task_id
        or run.plan_fingerprint != plan.fingerprint
        or run.state.value != "succeeded"
    ):
        raise _unavailable("Source automatic parse has not succeeded")
    run_refs = list(run.artifacts)
    for ref in run_refs:
        if ref.task_id != source_task_id:
            raise PipelineError("artifact_scope", "Automatic projection contains a cross-task reference")
        store.read(ref)
    try:
        execution_refs = execution.outputs(plan)
    except Exception as exc:
        raise _unavailable("Source automatic outputs are unavailable") from exc
    observed = {_ref_key(ref): ref for ref in [*run_refs, *execution_refs]}
    manifest_ref = _one(list(observed.values()), "manifest")
    if run_refs and _ref_key(manifest_ref) not in {_ref_key(ref) for ref in run_refs}:
        raise PipelineError("artifact_changed", "Automatic manifest is absent from the frozen projection")
    if execution_refs and _ref_key(manifest_ref) not in {_ref_key(ref) for ref in execution_refs}:
        raise PipelineError("artifact_changed", "Automatic manifest is absent from execution outputs")
    manifest = _json(store, manifest_ref, "Automatic manifest is not valid JSON")
    if (
        not isinstance(manifest, dict)
        or manifest.get("task_id") != source_task_id
        or manifest.get("plan_fingerprint") != plan.fingerprint
        or manifest.get("workflow_type") != plan.workflow_type
    ):
        raise PipelineError("artifact_changed", "Automatic manifest fingerprint or task changed")
    if manifest.get("inputs") != [ref.model_dump(mode="json") for ref in plan.inputs]:
        raise PipelineError("artifact_changed", "Automatic manifest inputs changed")
    try:
        declared = [ArtifactRef.model_validate(item) for item in manifest["outputs"]]
    except Exception as exc:
        raise PipelineError("artifact_changed", "Automatic manifest outputs are invalid") from exc
    if not declared or len({_ref_key(ref) for ref in declared}) != len(declared):
        raise PipelineError("artifact_changed", "Automatic manifest outputs are duplicated")
    for ref in declared:
        if ref.task_id != source_task_id:
            raise PipelineError("artifact_scope", "Automatic manifest contains a cross-task output")
        store.read(ref)
        if _ref_key(ref) not in observed:
            raise PipelineError("artifact_changed", "Automatic manifest output is absent from execution")
        if run_refs and _ref_key(ref) not in {_ref_key(item) for item in run_refs}:
            raise PipelineError("artifact_changed", "Automatic manifest output is absent from the frozen projection")
        if execution_refs and _ref_key(ref) not in {_ref_key(item) for item in execution_refs}:
            raise PipelineError("artifact_changed", "Automatic manifest output is absent from execution outputs")
    operations_ref = manifest.get("operations_ref")
    if operations_ref is not None:
        try:
            operations = ArtifactRef.model_validate(operations_ref)
        except Exception as exc:
            raise PipelineError("artifact_changed", "Automatic operation evidence is invalid") from exc
        if operations.task_id != source_task_id:
            raise PipelineError("artifact_scope", "Automatic operation evidence is cross-task")
        store.read(operations)
    source_result_ref = _one(declared, "sources")
    try:
        source_result = UIProvisionResult.model_validate_json(store.read(source_result_ref))
    except Exception as exc:
        raise PipelineError("artifact_changed", "Automatic source evidence is invalid") from exc
    if source_result.status != "ready" or len(source_result.sources) != 1:
        raise _unavailable("Automatic input must contain one ready source")
    source = source_result.sources[0]
    for ref, role in ((source.original_ref, "original"), (source.provenance_ref, "provenance")):
        if ref.task_id != source_task_id or ref.role != role:
            raise PipelineError("artifact_scope", "Automatic source provenance is outside the source task")
        store.read(ref)
    canonical_index_ref = _one(declared, "canonical_index")
    index = _json(store, canonical_index_ref, "Automatic canonical index is not valid JSON")
    try:
        canonical = CanonicalImage.model_validate(index["canonical"])
    except Exception as exc:
        raise PipelineError("artifact_changed", "Automatic canonical identity is invalid") from exc
    if (
        _ref_key(canonical.original_ref) != _ref_key(source.original_ref)
        or canonical.original_ref.role != "original"
        or canonical.canonical_ref.task_id != source_task_id
        or canonical.canonical_ref.role != "canonical"
    ):
        raise PipelineError("artifact_changed", "Automatic canonical source identity changed")
    store.read(canonical.canonical_ref)
    if _ref_key(canonical.canonical_ref) not in {_ref_key(ref) for ref in declared}:
        raise PipelineError("artifact_changed", "Automatic canonical is absent from the manifest")
    layout_ref = _one(declared, "layout")
    texts_ref = _one(declared, "texts")
    _validate_layout(store, layout_ref, canonical, texts_ref)
    _validate_texts(store, texts_ref, canonical)

    source_refs: dict[tuple[str, str, str], ArtifactRef] = {
        _ref_key(ref): ref for ref in [*declared, source.original_ref, source.provenance_ref]
    }
    # Layout, text and index payloads carry additional immutable references.
    # Include them in the copy closure so the rebinding never leaves a stale
    # source-task reference in the automatic layout.
    for ref in list(source_refs.values()):
        if ref.media_type == "application/json" or ref.role in {"layout", "texts", "canonical_index", "sources"}:
            payload = _json(store, ref, "Automatic evidence payload is not valid JSON")
            _collect_refs(payload, source_refs)
    source_refs.pop(_ref_key(manifest_ref), None)
    mapping: dict[tuple[str, str, str], ArtifactRef] = {}
    original_ref = store.put(
        new_task_id,
        "automatic-input",
        store.read(source.original_ref),
        role="original",
        media_type=source.original_ref.media_type,
    )
    mapping[_ref_key(source.original_ref)] = original_ref
    canonical_ref = store.put(
        new_task_id,
        "automatic-input",
        store.read(canonical.canonical_ref),
        role="canonical",
        media_type=canonical.canonical_ref.media_type,
        source_ids=[original_ref.artifact_id],
    )
    mapping[_ref_key(canonical.canonical_ref)] = canonical_ref
    deferred_roles = {"layout", "texts", "canonical_index", "sources", "provenance", "manifest"}
    for key, ref in source_refs.items():
        if key in mapping or ref.role in deferred_roles:
            continue
        copied = store.put(
            new_task_id,
            "automatic-input",
            store.read(ref),
            role=ref.role,
            media_type=ref.media_type,
            source_ids=[canonical_ref.artifact_id],
        )
        mapping[key] = copied
    provenance_payload = {
        "schema_version": 1,
        "origin": "automatic",
        "frozen": True,
        "source_task_id": source_task_id,
        "source_fingerprint": plan.fingerprint,
        "source_refs": _source_refs(source, source_result_ref),
        "canonical_ref": canonical.canonical_ref.model_dump(mode="json"),
        "layout_ref": layout_ref.model_dump(mode="json"),
        "texts_ref": texts_ref.model_dump(mode="json"),
    }
    provenance_ref = store.put(
        new_task_id,
        "automatic-input",
        canonical_json(provenance_payload).encode(),
        role="provenance",
        source_ids=[original_ref.artifact_id],
    )
    mapping[_ref_key(source.provenance_ref)] = provenance_ref
    rewritten_payloads = {}
    for old_ref, role in (
        (texts_ref, "texts"),
        (layout_ref, "layout"),
        (canonical_index_ref, "canonical_index"),
        (source_result_ref, "sources"),
    ):
        payload = _json(store, old_ref, "Automatic evidence payload is not valid JSON")
        rewritten = _rewrite_refs(payload, mapping)
        copied = store.put(
            new_task_id,
            "automatic-input",
            canonical_json(rewritten).encode(),
            role=role,
            media_type=old_ref.media_type,
            source_ids=[canonical_ref.artifact_id],
        )
        mapping[_ref_key(old_ref)] = copied
        rewritten_payloads[role] = copied
    # Complete the reference closure.  Some v1 payloads (analysis/view
    # evidence and the source provision result) contain nested JSON refs.  A
    # few bounded passes let dependencies settle without ever leaving an old
    # task ref inside a copied JSON artifact.
    for _ in range(4):
        changed = False
        for key, old_ref in source_refs.items():
            target_ref = mapping.get(key)
            if target_ref is None or old_ref.role in {"manifest", "provenance"}:
                continue
            if old_ref.media_type != "application/json":
                continue
            payload = _json(store, target_ref, "Rebound automatic evidence is invalid")
            rewritten = _rewrite_refs(payload, mapping)
            replacement = store.put(
                new_task_id,
                "automatic-input",
                canonical_json(rewritten).encode(),
                role=target_ref.role,
                media_type=target_ref.media_type,
                source_ids=[canonical_ref.artifact_id],
            )
            if replacement != target_ref:
                mapping[key] = replacement
                changed = True
        if not changed:
            break
    for role, old_ref in (
        ("texts", texts_ref),
        ("layout", layout_ref),
        ("canonical_index", canonical_index_ref),
        ("sources", source_result_ref),
    ):
        rewritten_payloads[role] = mapping[_ref_key(old_ref)]
    target_source = UISource(
        source_id=source.source_id,
        original_ref=original_ref,
        provenance_ref=provenance_ref,
        input_entry_ids=source.input_entry_ids,
    )
    entries = [
        UIInputEntry(entry_id=entry_id, status="ready", source_id=source.source_id)
        for entry_id in source.input_entry_ids
    ]
    input_manifest = UIInputManifest(task_id=new_task_id, status="ready", entries=entries, sources=[target_source])
    metadata_ref = store.put(
        new_task_id,
        "automatic-input",
        canonical_json(input_manifest).encode(),
        role="input_manifest",
        source_ids=[original_ref.artifact_id, provenance_ref.artifact_id],
    )
    target_refs = {
        "canonical_ref": canonical_ref,
        "layout_ref": rewritten_payloads["layout"],
        "texts_ref": rewritten_payloads["texts"],
    }
    automatic_manifest = {
        "schema_version": 1,
        "kind": "ui_automatic_input",
        "task_id": new_task_id,
        "source_task_id": source_task_id,
        "source_fingerprint": plan.fingerprint,
        "layout_origin": "automatic",
        "evaluation_lane": "automatic",
        "model_calls": 0,
        "external_calls": 0,
        "source_refs": {
            **_source_refs(source, source_result_ref),
            "manifest_ref": manifest_ref.model_dump(mode="json"),
            "canonical_ref": canonical.canonical_ref.model_dump(mode="json"),
            "layout_ref": layout_ref.model_dump(mode="json"),
            "texts_ref": texts_ref.model_dump(mode="json"),
        },
        "outputs": {
            "input_manifest_ref": metadata_ref.model_dump(mode="json"),
            "original_ref": original_ref.model_dump(mode="json"),
            "provenance_ref": provenance_ref.model_dump(mode="json"),
            **{name: ref.model_dump(mode="json") for name, ref in target_refs.items()},
        },
    }
    automatic_manifest_ref = store.put(
        new_task_id,
        "automatic-input",
        canonical_json(automatic_manifest).encode(),
        role="automatic_manifest",
        source_ids=[metadata_ref.artifact_id, canonical_ref.artifact_id, rewritten_payloads["layout"].artifact_id],
    )
    info = {
        "source_task_id": source_task_id,
        "source_fingerprint": plan.fingerprint,
        "model_calls": 0,
        "layout_origin": "automatic",
        "automatic_manifest_ref": automatic_manifest_ref,
    }
    refs = {**target_refs, "automatic_manifest_ref": automatic_manifest_ref}
    return ManualUIInput(inputs=[original_ref], metadata_ref=metadata_ref), info, refs


__all__ = ["automatic_input"]
