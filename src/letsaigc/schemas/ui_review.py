"""Local human review content; separate from model output and approval DTOs."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .pipeline import ArtifactRef, Identifier
from .ui import UIContent

BaseType = Literal["text", "image", "container", "other"]
Tag = Literal["icon", "character_illustration", "illustration", "background", "button", "panel", "map", "bar"]
Box = Annotated[list[Annotated[int, Field(strict=True)]], Field(min_length=4, max_length=4)]
FieldName = Literal["bbox", "base_type", "semantic_tags", "parent_id", "text_region_ids", "text"]


class ReviewText(UIContent):
    text_region_id: Identifier
    effective_text: str = Field(max_length=4096)
    bbox: Box
    origin: Literal["human", "ocr"] = "human"
    ocr_text_id: Identifier | None = None
    original_text: str | None = None
    original_score: float | None = None
    original_polygon: list[list[float]] | None = None
    geometry_origin: Literal["human", "ocr"] = "human"

    @model_validator(mode="after")
    def source(self):
        if self.ocr_text_id is None and any(
            x is not None for x in (self.original_text, self.original_score, self.original_polygon)
        ):
            raise ValueError("Human text cannot manufacture OCR observations")
        if self.origin == "ocr" and self.ocr_text_id is None:
            raise ValueError("OCR source is required")
        return self


class ReviewElement(UIContent):
    element_id: Identifier
    base_type: BaseType
    semantic_tags: list[Tag] = Field(default_factory=list, max_length=8)
    bbox: Box
    parent_id: Identifier | None = None
    text_region_ids: list[Identifier] = Field(default_factory=list, max_length=4096)
    locked_fields: list[FieldName] = Field(default_factory=list, max_length=6)
    field_sources: dict[str, Literal["model", "ocr", "human"]] = Field(default_factory=dict)
    replaces_ids: list[Identifier] = Field(default_factory=list, max_length=512)


class ReviewDocument(UIContent):
    schema_version: Literal[2] = 2
    task_id: Identifier
    source_id: Identifier
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    elements: list[ReviewElement] = Field(default_factory=list, max_length=512)
    texts: list[ReviewText] = Field(default_factory=list, max_length=4096)
    user_declared: str = Field(default="", max_length=4096)

    @model_validator(mode="after")
    def graph(self):
        if self.width * self.height > 32_000_000:
            raise ValueError("Review pixel limit")
        elements = {x.element_id: x for x in self.elements}
        texts = {x.text_region_id for x in self.texts}
        if len(elements) != len(self.elements) or len(texts) != len(self.texts):
            raise ValueError("Duplicate review identity")
        for item in [*self.elements, *self.texts]:
            x1, y1, x2, y2 = item.bbox
            if not 0 <= x1 < x2 <= self.width or not 0 <= y1 < y2 <= self.height:
                raise ValueError("Invalid review rectangle")
        for item in self.elements:
            if len(item.text_region_ids) != len(set(item.text_region_ids)) or not set(item.text_region_ids) <= texts:
                raise ValueError("Invalid text association")
            if len(item.semantic_tags) != len(set(item.semantic_tags)):
                raise ValueError("Duplicate semantic tag")
            seen, current = {item.element_id}, item.parent_id
            while current is not None:
                if current in seen or current not in elements:
                    raise ValueError("Invalid parent relationship")
                seen.add(current)
                current = elements[current].parent_id
        return self


class ReviewAction(UIContent):
    action: Literal[
        "add_region",
        "update_box",
        "set_type",
        "set_tags",
        "set_parent",
        "set_text_links",
        "set_text",
        "delete_region",
        "set_lock",
        "restore_revision",
    ]
    element_id: Identifier | None = None
    text_region_id: Identifier | None = None
    bbox: Box | None = None
    base_type: BaseType | None = None
    semantic_tags: list[Tag] | None = None
    parent_id: Identifier | None = None
    text_region_ids: list[Identifier] | None = None
    text: str | None = Field(default=None, max_length=4096)
    children: Literal["detach_children", "delete_subtree"] | None = None
    fields: list[FieldName] | None = None
    replaces_ids: list[Identifier] | None = None
    revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def arguments(self):
        allowed = {
            "add_region": {"element_id", "bbox", "base_type", "text", "semantic_tags", "replaces_ids"},
            "update_box": {"element_id", "bbox"},
            "set_type": {"element_id", "base_type"},
            "set_tags": {"element_id", "semantic_tags"},
            "set_parent": {"element_id", "parent_id"},
            "set_text_links": {"element_id", "text_region_ids"},
            "set_text": {"text_region_id", "text"},
            "delete_region": {"element_id", "children"},
            "set_lock": {"element_id", "fields"},
            "restore_revision": {"revision"},
        }[self.action]
        present = self.model_fields_set - {"action"}
        required = allowed - {"text", "semantic_tags", "replaces_ids"} if self.action == "add_region" else allowed
        if self.action == "delete_region":
            required = {"element_id"}
        if present - allowed or not required <= present:
            raise ValueError("Action arguments do not match the declared operation")
        for name in required - {"parent_id"}:
            if getattr(self, name) is None:
                raise ValueError("Missing action value")
        return self


class ReviewPatch(UIContent):
    request_id: Identifier
    base_draft_revision: int = Field(default=0, ge=0)
    base_confirmed_revision: int | None = Field(default=None, ge=0)
    actions: list[ReviewAction] = Field(max_length=256)


class ReviewConfirm(UIContent):
    request_id: Identifier
    draft_revision: int = Field(ge=0)
    expected_confirmed_revision: int | None = Field(default=None, ge=0)


class ReviewedLayoutBinding(UIContent):
    task_id: Identifier
    review_revision: int = Field(ge=0)
    review_manifest_ref: ArtifactRef
    layout_ref: ArtifactRef
    texts_ref: ArtifactRef
    canonical_ref: ArtifactRef
    evaluation_lane: Literal["human_assisted"] = "human_assisted"

    @model_validator(mode="after")
    def scope(self):
        for name, role in (
            ("review_manifest_ref", "review_manifest"),
            ("layout_ref", "review_layout"),
            ("texts_ref", "review_texts"),
        ):
            ref = getattr(self, name)
            if ref.task_id != self.task_id or ref.role != role:
                raise ValueError("Review binding scope or role mismatch")
        if self.canonical_ref.task_id != self.task_id:
            raise ValueError("Canonical scope mismatch")
        return self
