"""Pure, provider-free local revision planning for T029.

This module describes a bounded revision request and computes the affected
dependency closure.  It does not read a ledger, call a model, or grant an
approval.  A caller supplies the immutable artifact projection from the base
plan; unchanged artifact references are returned verbatim for reuse.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, Digest, Identifier, PipelineModel

MAX_COORDINATE = 8192
MAX_PROMPT_LENGTH = 4096
MAX_NOTES_LENGTH = 2000


class RevisionAction(StrEnum):
    reread_text = "reread_text"
    adjust_segmentation = "adjust_segmentation"
    review_region = "review_region"
    regenerate = "regenerate"


class RevisionNode(StrEnum):
    normalize = "normalize"
    ocr = "ocr"
    analyze = "analyze"
    layout = "layout"
    crop = "crop"
    segmentation = "segmentation"
    glyphs = "glyphs"
    mask = "mask"
    inpaint = "inpaint"
    manifest = "manifest"


class RevisionSegmentationPrompt(PipelineModel):
    """A bounded local SAM prompt replacement for one stable element."""

    element_id: Identifier
    box: tuple[int, int, int, int]
    points: list[tuple[float, float]] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_geometry(self):
        x1, y1, x2, y2 = self.box
        if not (0 <= x1 < x2 <= MAX_COORDINATE and 0 <= y1 < y2 <= MAX_COORDINATE):
            raise ValueError("Segmentation prompt box is outside the bounded image coordinate space")
        if any(
            not (0 <= x <= MAX_COORDINATE and 0 <= y <= MAX_COORDINATE)
            for point in self.points
            for x, y in (point,)
        ):
            raise ValueError("Segmentation prompt point is outside the bounded image coordinate space")
        return self


class RevisionRequest(PipelineModel):
    """An explicit, immutable request to revise one bounded set of IDs."""

    schema_version: Literal[1] = 1
    base_task_id: Identifier
    base_fingerprint: Digest
    base_revision: int = Field(ge=0, le=2, strict=True)
    action: RevisionAction
    target_ids: list[Identifier] = Field(min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=8)

    @model_validator(mode="after")
    def validate_action_parameters(self):
        if len(set(self.target_ids)) != len(self.target_ids):
            raise ValueError("Revision target_ids must be unique")
        keys = set(self.parameters)
        forbidden = {"steps", "cfg", "denoise", "mask", "model", "recipe"}
        if keys & forbidden:
            raise ValueError("Revision parameters cannot expand model, recipe, mask, or sampling scope")
        if self.action is RevisionAction.reread_text:
            if keys:
                raise ValueError("reread_text does not accept parameters")
        elif self.action is RevisionAction.review_region:
            if keys - {"user_notes"}:
                raise ValueError("review_region only accepts user_notes")
            notes = self.parameters.get("user_notes")
            if notes is not None and (not isinstance(notes, str) or len(notes) > MAX_NOTES_LENGTH):
                raise ValueError("user_notes is outside its bounded text limit")
        elif self.action is RevisionAction.adjust_segmentation:
            if keys != {"prompts"}:
                raise ValueError("adjust_segmentation requires only prompts")
            prompts = self.parameters["prompts"]
            if not isinstance(prompts, list) or not 1 <= len(prompts) <= 64:
                raise ValueError("adjust_segmentation prompts are outside their bounded limit")
            parsed = [RevisionSegmentationPrompt.model_validate(item) for item in prompts]
            prompt_ids = [item.element_id for item in parsed]
            if len(set(prompt_ids)) != len(prompt_ids):
                raise ValueError("adjust_segmentation prompt element IDs must be unique")
            if not set(prompt_ids).issubset(self.target_ids):
                raise ValueError("adjust_segmentation prompts must target the requested elements")
        elif self.action is RevisionAction.regenerate:
            if keys - {"prompt", "negative_prompt", "seed"}:
                raise ValueError("regenerate only accepts prompt, negative_prompt, and seed")
            for name in ("prompt", "negative_prompt"):
                value = self.parameters.get(name)
                if value is not None and (not isinstance(value, str) or len(value) > MAX_PROMPT_LENGTH):
                    raise ValueError(f"{name} is outside its bounded text limit")
            seed = self.parameters.get("seed")
            if seed is not None and (type(seed) is not int or not 0 <= seed <= 2**63 - 1):
                raise ValueError("seed is outside its bounded integer limit")
        return self


class RevisionArtifact(PipelineModel):
    """An immutable artifact and the stable IDs it represents."""

    node: RevisionNode
    artifact_ref: ArtifactRef
    target_ids: list[Identifier] = Field(default_factory=list, max_length=8192)

    @model_validator(mode="after")
    def validate_target_ids(self):
        if len(set(self.target_ids)) != len(self.target_ids):
            raise ValueError("Revision artifact target_ids must be unique")
        return self


class DependencyClosure(PipelineModel):
    roots: list[RevisionNode] = Field(min_length=1)
    nodes: list[RevisionNode] = Field(min_length=1)
    affected_target_ids: list[Identifier] = Field(min_length=1, max_length=64)


class RevisionProjection(PipelineModel):
    request: RevisionRequest
    closure: DependencyClosure
    recompute: list[RevisionArtifact] = Field(default_factory=list, max_length=128)
    reuse: list[RevisionArtifact] = Field(default_factory=list, max_length=128)
    effective_reuse: list[RevisionArtifact] = Field(default_factory=list, max_length=128)
    proposal_only: bool = False
    recompute_requires_adoption: bool = False
    new_plan_required: bool = False
    approval_required: bool = False
    mutable_parameters: list[str] = Field(default_factory=list, max_length=3)


# Values are the immediate upstream dependencies.  The order is also the
# stable serialization order used in projections and tests.
DEFAULT_DEPENDENCY_GRAPH: dict[str, tuple[str, ...]] = {
    RevisionNode.normalize.value: (),
    RevisionNode.ocr.value: (RevisionNode.normalize.value,),
    RevisionNode.analyze.value: (RevisionNode.normalize.value, RevisionNode.ocr.value),
    RevisionNode.layout.value: (RevisionNode.analyze.value, RevisionNode.ocr.value),
    RevisionNode.crop.value: (RevisionNode.layout.value,),
    RevisionNode.segmentation.value: (RevisionNode.layout.value,),
    RevisionNode.glyphs.value: (RevisionNode.ocr.value, RevisionNode.segmentation.value),
    RevisionNode.mask.value: (RevisionNode.segmentation.value, RevisionNode.glyphs.value),
    RevisionNode.inpaint.value: (RevisionNode.mask.value,),
    RevisionNode.manifest.value: (
        RevisionNode.layout.value,
        RevisionNode.crop.value,
        RevisionNode.segmentation.value,
        RevisionNode.glyphs.value,
        RevisionNode.mask.value,
        RevisionNode.inpaint.value,
    ),
}

_NODE_ORDER = tuple(DEFAULT_DEPENDENCY_GRAPH)
_ACTION_ROOTS = {
    RevisionAction.reread_text: (RevisionNode.ocr.value,),
    RevisionAction.adjust_segmentation: (RevisionNode.segmentation.value,),
    RevisionAction.review_region: (RevisionNode.analyze.value,),
    RevisionAction.regenerate: (RevisionNode.inpaint.value,),
}


def _validate_graph(graph: Mapping[str, Iterable[str]]) -> dict[str, tuple[str, ...]]:
    if not isinstance(graph, Mapping) or not graph:
        raise PipelineError("invalid_dependency_graph", "Revision dependency graph must be non-empty")
    normalized = {str(node): tuple(str(parent) for parent in parents) for node, parents in graph.items()}
    keys = set(normalized)
    dangling = {parent for parents in normalized.values() for parent in parents} - keys
    if dangling:
        raise PipelineError("invalid_dependency_graph", "Revision dependency graph has a dangling node")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str):
        if node in visiting:
            raise PipelineError("dependency_cycle", "Revision dependency graph contains a cycle")
        if node in visited:
            return
        visiting.add(node)
        for parent in normalized[node]:
            visit(parent)
        visiting.remove(node)
        visited.add(node)

    for node in normalized:
        visit(node)
    return normalized


def _stable_id_set(values: Iterable[str], *, code: str = "unknown_target") -> set[str]:
    result = set()
    for value in values:
        if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) is None:
            raise PipelineError(code, "Revision target ID is invalid")
        result.add(value)
    return result


def _downstream(graph: Mapping[str, tuple[str, ...]], roots: set[str]) -> set[str]:
    affected = set(roots)
    changed = True
    while changed:
        changed = False
        for node, parents in graph.items():
            if node not in affected and set(parents) & affected:
                affected.add(node)
                changed = True
    return affected


def dependency_closure(
    request: RevisionRequest,
    *,
    known_target_ids: Iterable[str] | None = None,
    graph: Mapping[str, Iterable[str]] = DEFAULT_DEPENDENCY_GRAPH,
) -> DependencyClosure:
    """Return the stable downstream closure for a validated revision request."""
    normalized = _validate_graph(graph)
    roots = set(_ACTION_ROOTS[request.action])
    missing_roots = roots - set(normalized)
    if missing_roots:
        raise PipelineError("invalid_dependency_graph", "Revision action root is absent from the graph")
    if known_target_ids is not None:
        known = _stable_id_set(known_target_ids)
        if not set(request.target_ids).issubset(known):
            raise PipelineError("unknown_target", "Revision request references an unknown stable ID")
    nodes = _downstream(normalized, roots)
    order = [node for node in _NODE_ORDER if node in nodes]
    order.extend(sorted(nodes - set(order)))
    return DependencyClosure(
        roots=[RevisionNode(node) for node in _ACTION_ROOTS[request.action]],
        nodes=[RevisionNode(node) for node in order],
        affected_target_ids=list(request.target_ids),
    )


def _coerce_artifacts(artifacts: Sequence[RevisionArtifact] | Mapping[str, Any]) -> list[RevisionArtifact]:
    if isinstance(artifacts, Mapping):
        values: list[RevisionArtifact] = []
        for node, items in artifacts.items():
            candidates = items if isinstance(items, Sequence) and not isinstance(items, (str, bytes)) else [items]
            for item in candidates:
                if isinstance(item, RevisionArtifact):
                    values.append(item)
                elif isinstance(item, ArtifactRef):
                    values.append(RevisionArtifact(node=node, artifact_ref=item))
                else:
                    value = dict(item)
                    value.setdefault("node", node)
                    values.append(RevisionArtifact.model_validate(value))
        return values
    return [item if isinstance(item, RevisionArtifact) else RevisionArtifact.model_validate(item) for item in artifacts]


def project_revision(
    request: RevisionRequest,
    artifacts: Sequence[RevisionArtifact] | Mapping[str, Any],
    *,
    known_target_ids: Iterable[str] | None = None,
    graph: Mapping[str, Iterable[str]] = DEFAULT_DEPENDENCY_GRAPH,
) -> RevisionProjection:
    """Project affected artifacts while preserving exact refs outside the closure.

    For reread/review actions, ``recompute`` describes what would change after
    a human adopts the model suggestion.  ``effective_reuse`` remains the full
    current projection until that adoption occurs.
    """
    values = _coerce_artifacts(artifacts)
    observed_ids = {target for item in values for target in item.target_ids}
    closure = dependency_closure(
        request,
        known_target_ids=observed_ids if known_target_ids is None else known_target_ids,
        graph=graph,
    )
    affected_nodes = set(closure.nodes)
    targets = set(request.target_ids)
    recompute: list[RevisionArtifact] = []
    reusable: list[RevisionArtifact] = []
    for item in values:
        selected = item.node.value in affected_nodes and (
            not item.target_ids or bool(set(item.target_ids) & targets)
        )
        (recompute if selected else reusable).append(item)
    proposal_only = request.action in {RevisionAction.reread_text, RevisionAction.review_region}
    effective_reuse = values if proposal_only else reusable
    mutable = (
        [key for key in ("prompt", "negative_prompt", "seed") if key in request.parameters]
        if request.action is RevisionAction.regenerate
        else []
    )
    return RevisionProjection(
        request=request,
        closure=closure,
        recompute=recompute,
        reuse=reusable,
        effective_reuse=effective_reuse,
        proposal_only=proposal_only,
        recompute_requires_adoption=proposal_only,
        new_plan_required=request.action is RevisionAction.adjust_segmentation,
        approval_required=request.action in {RevisionAction.adjust_segmentation, RevisionAction.regenerate},
        mutable_parameters=mutable,
    )


__all__ = [
    "DEFAULT_DEPENDENCY_GRAPH",
    "DependencyClosure",
    "RevisionAction",
    "RevisionArtifact",
    "RevisionNode",
    "RevisionProjection",
    "RevisionRequest",
    "RevisionSegmentationPrompt",
    "dependency_closure",
    "project_revision",
]
