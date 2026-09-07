"""Immutable model suggestions for the local human review flow.

Suggestions are evidence-bearing artifacts.  Saving one never changes a review
head; applying one is a separate, explicit operation which delegates to the
existing review repository CAS and lock checks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import Field, TypeAdapter

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, Digest, Identifier, PipelineModel, digest
from ..schemas.ui_review import ReviewAction, ReviewDocument, ReviewedLayoutBinding, ReviewPatch

_SUGGESTION_ROLE = "review_suggestion"
_ALLOWED_ACTIONS = {
    "update_box",
    "set_type",
    "set_tags",
    "set_parent",
    "set_text_links",
    "set_text",
}
_ACTION_FIELDS = {
    "update_box": "bbox",
    "set_type": "base_type",
    "set_tags": "semantic_tags",
    "set_parent": "parent_id",
    "set_text_links": "text_region_ids",
}


class _SuggestionRecord(PipelineModel):
    schema_version: Literal[1] = 1
    kind: Literal["ui_review_suggestion"] = "ui_review_suggestion"
    task_id: Identifier
    review_revision: int = Field(ge=0, strict=True)
    review_hash: Digest
    binding_hash: Digest
    review_manifest_sha256: Digest
    base_draft_revision: int = Field(ge=0, strict=True)
    base_confirmed_revision: int | None = Field(default=None, ge=0, strict=True)
    binding: ReviewedLayoutBinding
    target_ids: list[Identifier] = Field(min_length=1, max_length=512)
    actions: list[dict[str, Any]] = Field(min_length=1, max_length=256)
    evidence_task_ids: list[Identifier] = Field(min_length=1, max_length=32)
    evidence_refs: list[ArtifactRef] = Field(min_length=1, max_length=64)


def _error(code: str, message: str = "Review suggestion is invalid") -> PipelineError:
    return PipelineError(code, message)


def _repository(service):
    from .review import ReviewRepository

    return ReviewRepository(service.ledger, service.artifacts)


def _as_binding(value: ReviewedLayoutBinding | Mapping[str, Any]) -> ReviewedLayoutBinding:
    try:
        return ReviewedLayoutBinding.model_validate(
            value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        )
    except Exception as exc:
        raise _error("review_binding_invalid") from exc


def _as_ref(value: ArtifactRef | Mapping[str, Any]) -> ArtifactRef:
    try:
        return ArtifactRef.model_validate(value.model_dump(mode="json") if hasattr(value, "model_dump") else value)
    except Exception as exc:
        raise _error("artifact_scope") from exc


def _identifier(value: Any, code: str = "review_suggestion_invalid") -> str:
    try:
        return TypeAdapter(Identifier).validate_python(value)
    except Exception as exc:
        raise _error(code) from exc


def _task_ids(values: Sequence[str] | None, default: str) -> list[str]:
    raw = [default] if values is None else list(values)
    if not raw or len(raw) > 32:
        raise _error("review_suggestion_invalid")
    result = [_identifier(value) for value in raw]
    if len(set(result)) != len(result):
        raise _error("review_suggestion_invalid")
    return result


def _actions(values: Sequence[ReviewAction | Mapping[str, Any]]) -> list[ReviewAction]:
    if isinstance(values, (str, bytes)):
        raise _error("review_suggestion_invalid")
    result: list[ReviewAction] = []
    try:
        for value in values:
            result.append(
                ReviewAction.model_validate(
                    value.model_dump(exclude_unset=True, mode="json")
                    if hasattr(value, "model_dump")
                    else value
                )
            )
    except Exception as exc:
        raise _error("review_suggestion_invalid") from exc
    if not result or len(result) > 256:
        raise _error("review_suggestion_invalid")
    return result


def _verify_binding(service, binding: ReviewedLayoutBinding) -> tuple[Any, dict[str, Any]]:
    """Resolve the canonical confirmed binding and its current review heads."""

    repository = _repository(service)
    try:
        current = repository.binding(binding.task_id, binding.review_revision)
    except PipelineError:
        raise
    if current != binding:
        raise _error("review_input_changed")
    head = repository.head(binding.task_id)
    if head["draft_revision"] != binding.review_revision or head["confirmed_revision"] != binding.review_revision:
        raise _error("review_conflict")
    return repository, head


def _binding_hash(binding: ReviewedLayoutBinding) -> str:
    return digest(binding.model_dump(mode="json"))


def _target_and_field(action: ReviewAction) -> tuple[str, str | None]:
    if action.action == "set_text":
        return action.text_region_id or "", "text"
    return action.element_id or "", _ACTION_FIELDS.get(action.action)


def _validate_actions(
    document: ReviewDocument,
    target_ids: Sequence[str],
    actions: Sequence[ReviewAction],
    *,
    reject_locked: bool,
) -> None:
    targets = set(target_ids)
    elements = {item.element_id: item for item in document.elements}
    texts = {item.text_region_id: item for item in document.texts}
    for action in actions:
        if action.action not in _ALLOWED_ACTIONS:
            raise _error("review_suggestion_invalid")
        target, field = _target_and_field(action)
        if not target or target not in targets:
            raise _error("review_target_scope")
        if action.action == "set_text":
            text = texts.get(target)
            if text is None:
                raise _error("review_target_scope")
            if reject_locked and any(
                target in element.text_region_ids and "text" in element.locked_fields for element in elements.values()
            ):
                raise _error("review_locked")
            # A suggestion is allowed to propose a correction to either OCR or
            # an earlier human value; only the explicit acceptance is mutating.
            continue
        element = elements.get(target)
        if element is None:
            raise _error("review_target_scope")
        if reject_locked and field in element.locked_fields:
            raise _error("review_locked")
        if action.action == "set_parent" and action.parent_id is not None:
            if action.parent_id not in elements or action.parent_id not in targets:
                raise _error("review_target_scope")
        if action.action == "set_text_links":
            linked = action.text_region_ids or []
            if any(value not in texts or value not in targets for value in linked):
                raise _error("review_target_scope")


def _verify_evidence(service, binding: ReviewedLayoutBinding, evidence_refs, evidence_task_ids):
    allowed = _task_ids(evidence_task_ids, binding.task_id)
    refs = [_as_ref(value) for value in evidence_refs]
    if not refs or len(refs) > 64:
        raise _error("review_suggestion_invalid")
    seen: set[str] = set()
    for ref in refs:
        if ref.task_id not in allowed:
            raise _error("artifact_scope")
        if ref.artifact_id in seen:
            raise _error("review_suggestion_invalid")
        seen.add(ref.artifact_id)
        service.artifacts.read(ref)
    return allowed, refs


def save_suggestion(
    service,
    review_binding: ReviewedLayoutBinding | Mapping[str, Any],
    target_ids: Sequence[str],
    actions: Sequence[ReviewAction | Mapping[str, Any]],
    evidence_refs: Sequence[ArtifactRef | Mapping[str, Any]],
    *,
    evidence_task_ids: Sequence[str] | None = None,
) -> ArtifactRef:
    """Persist a model suggestion without changing the review head.

    ``evidence_task_ids`` is an explicit root integration boundary for model
    evidence produced by an approved child in the same edit chain.  Without it,
    evidence must belong to the reviewed task itself.
    """

    binding = _as_binding(review_binding)
    repository, head = _verify_binding(service, binding)
    try:
        targets = [_identifier(value, "review_target_scope") for value in target_ids]
    except PipelineError:
        raise
    if not targets or len(targets) > 512 or len(set(targets)) != len(targets):
        raise _error("review_suggestion_invalid")
    parsed_actions = _actions(actions)
    _validate_actions(
        repository.document(binding.task_id, binding.review_revision),
        targets,
        parsed_actions,
        reject_locked=True,
    )
    allowed_tasks, refs = _verify_evidence(service, binding, evidence_refs, evidence_task_ids)
    binding_hash = _binding_hash(binding)
    payload = _SuggestionRecord(
        task_id=binding.task_id,
        review_revision=binding.review_revision,
        review_hash=binding.review_manifest_ref.sha256,
        binding_hash=binding_hash,
        review_manifest_sha256=binding.review_manifest_ref.sha256,
        base_draft_revision=head["draft_revision"],
        base_confirmed_revision=head["confirmed_revision"],
        binding=binding,
        target_ids=targets,
        actions=[action.model_dump(exclude_unset=True, mode="json") for action in parsed_actions],
        evidence_task_ids=allowed_tasks,
        evidence_refs=refs,
    )
    operation = "review-suggestion-" + digest(payload.model_dump(mode="json"))[:48]
    source_ids = [
        binding.review_manifest_ref.artifact_id,
        binding.layout_ref.artifact_id,
        binding.texts_ref.artifact_id,
        binding.canonical_ref.artifact_id,
        *(ref.artifact_id for ref in refs),
    ]
    return service.artifacts.put(
        binding.task_id,
        operation,
        payload.model_dump_json().encode("utf-8"),
        role=_SUGGESTION_ROLE,
        source_ids=list(dict.fromkeys(source_ids)),
    )


def _read_suggestion(service, suggestion_ref: ArtifactRef | Mapping[str, Any]) -> tuple[ArtifactRef, _SuggestionRecord]:
    ref = _as_ref(suggestion_ref)
    if ref.role != _SUGGESTION_ROLE:
        raise _error("artifact_scope")
    try:
        record = _SuggestionRecord.model_validate_json(service.artifacts.read(ref))
    except PipelineError:
        raise
    except Exception as exc:
        raise _error("artifact_changed") from exc
    if record.task_id != ref.task_id or record.binding.task_id != ref.task_id:
        raise _error("artifact_scope")
    if record.review_hash != record.binding.review_manifest_ref.sha256:
        raise _error("artifact_changed")
    if record.binding_hash != _binding_hash(record.binding):
        raise _error("artifact_changed")
    if record.review_manifest_sha256 != record.binding.review_manifest_ref.sha256:
        raise _error("artifact_changed")
    for value in (
        record.binding.review_manifest_ref,
        record.binding.layout_ref,
        record.binding.texts_ref,
        record.binding.canonical_ref,
        *record.evidence_refs,
    ):
        if value.task_id not in record.evidence_task_ids and value.task_id != record.task_id:
            raise _error("artifact_scope")
        service.artifacts.read(value)
    return ref, record


def accept_suggestion(
    service,
    suggestion_ref: ArtifactRef | Mapping[str, Any],
    request_id: str,
    *,
    base_draft_revision: int,
    base_confirmed_revision: int | None,
) -> dict[str, Any]:
    """Explicitly apply one suggestion as a new human review draft revision."""

    request_id = _identifier(request_id)
    ref, record = _read_suggestion(service, suggestion_ref)
    if type(base_draft_revision) is not int or base_draft_revision < 0:
        raise _error("review_conflict")
    if base_confirmed_revision is not None and (
        type(base_confirmed_revision) is not int or base_confirmed_revision < 0
    ):
        raise _error("review_conflict")
    if (base_draft_revision, base_confirmed_revision) != (
        record.base_draft_revision,
        record.base_confirmed_revision,
    ):
        raise _error("review_conflict")
    parsed_actions = _actions(record.actions)
    patch = ReviewPatch(
        request_id=request_id,
        base_draft_revision=base_draft_revision,
        base_confirmed_revision=base_confirmed_revision,
        actions=parsed_actions,
    )
    repository = _repository(service)
    with repository.ledger.transaction() as db:
        _, previous = repository._existing(db, record.task_id, patch, "save")
    if previous and previous.get("state") == "saved":
        return {"request_id": request_id, "state": "accepted", "revision": previous["revision"],
                "suggestion_ref": ref.model_dump(mode="json")}
    repository, head = _verify_binding(service, record.binding)
    if (head["draft_revision"], head["confirmed_revision"]) != (
        base_draft_revision,
        base_confirmed_revision,
    ):
        raise _error("review_conflict")
    document = repository.document(record.task_id, base_draft_revision)
    # Re-check current locks so a lock added after suggestion creation cannot
    # be bypassed by explicit acceptance.
    _validate_actions(document, record.target_ids, parsed_actions, reject_locked=True)
    result = repository.save(record.task_id, patch)
    return {
        "request_id": request_id,
        "state": "accepted",
        "revision": result["revision"],
        "suggestion_ref": ref.model_dump(mode="json"),
    }


__all__ = ["accept_suggestion", "save_suggestion"]
