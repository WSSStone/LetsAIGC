"""UI preview contracts. Local content is distinct from safe transport DTOs."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent import TaskBudget
from .pipeline import ArtifactRef, Cost, Digest, Identifier, PipelineModel, digest
from .ui_provider import ManualUIInput, UIInputSpec


class UIContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, strict=True)


class UIResourceLimits(PipelineModel):
    max_image_bytes: int = Field(default=25 * 1024**2, gt=0, le=25 * 1024**2, strict=True)
    max_pixels: int = Field(default=32_000_000, gt=0, le=32_000_000, strict=True)
    max_image_edge: int = Field(default=8192, gt=0, le=8192, strict=True)
    ram_bytes: int = Field(default=24 * 1024**3, gt=0, le=24 * 1024**3, strict=True)
    vram_bytes: int = Field(default=10 * 1024**3, ge=0, le=10 * 1024**3, strict=True)
    minimum_free_disk_bytes: int = Field(default=16 * 1024**3, ge=16 * 1024**3, strict=True)
    temporary_bytes: int = Field(default=8 * 1024**3, gt=0, le=8 * 1024**3, strict=True)
    network_bytes: int = Field(default=1024**3, ge=0, le=1024**3, strict=True)
    active_seconds: int = Field(default=3600, gt=0, le=3600, strict=True)
    ocr_tile_size: Literal[1024] = 1024
    ocr_tile_overlap: Literal[64] = 64
    ocr_max_tiles: int = Field(default=64, ge=1, le=64, strict=True)
    vlm_longest_edge: int = Field(default=1536, ge=1, le=1536, strict=True)
    vlm_local_views: int = Field(default=3, ge=0, le=3, strict=True)


class UICallLimits(PipelineModel):
    query_plans: int = Field(default=1, ge=0, le=1, strict=True)
    queries: int = Field(default=3, ge=1, le=3, strict=True)
    search_attempts: int = Field(default=3, ge=1, le=3, strict=True)
    provider_switches: int = Field(default=2, ge=0, le=2, strict=True)
    candidates_per_query: int = Field(default=20, ge=1, le=20, strict=True)
    downloads_per_query: int = Field(default=5, ge=1, le=5, strict=True)
    vlm_calls_per_image: int = Field(default=4, ge=1, le=4, strict=True)
    ocr_rereads_per_image: int = Field(default=2, ge=0, le=2, strict=True)
    generation_revisions: int = Field(default=2, ge=0, le=2, strict=True)


class UIAnalysisRequest(PipelineModel):
    schema_version: Literal[1] = 1
    input: UIInputSpec
    output_mode: Literal["parse", "decompose", "reconstruct"] = "parse"
    language: Literal["auto", "zh", "en"] = "auto"
    reconstruction_target: Literal["scene_background", "map_surface"] | None = None
    selection_mode: Literal["none", "bound", "deferred"] = "none"
    selection_ref: ArtifactRef | None = None
    text_assets: bool = Field(default=False, strict=True)
    remove_text: bool = Field(default=False, strict=True)
    allow_local_revision: bool = Field(default=False, strict=True)
    budget: TaskBudget
    resources: UIResourceLimits = Field(default_factory=UIResourceLimits)
    limits: UICallLimits = Field(default_factory=UICallLimits)
    batch_failure_policy: Literal["continue_independent", "stop_on_error"] = "continue_independent"
    model_bindings: dict[Identifier, ArtifactRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent_request(self):
        parse = self.output_mode == "parse"
        if parse and (self.selection_mode != "none" or self.text_assets or self.remove_text):
            raise ValueError("Parse cannot request editing or text assets")
        if not parse and self.selection_mode == "none":
            raise ValueError("Editing needs a bound selection or automatic deferred proposal")
        if (self.selection_mode == "bound") != (self.selection_ref is not None):
            raise ValueError("Only a bound selection accepts a selection reference")
        if (self.output_mode == "reconstruct") != (self.reconstruction_target is not None):
            raise ValueError("Reconstruction target is required only for reconstruction")
        if self.remove_text and self.output_mode != "reconstruct":
            raise ValueError("Text removal requires reconstruction")
        if self.budget.max_revisions > self.limits.generation_revisions:
            raise ValueError("UI revisions exceed the requested maximum")
        return self

    def references(self) -> list[ArtifactRef]:
        if isinstance(self.input, ManualUIInput):
            refs = [*self.input.inputs, *([self.input.metadata_ref] if self.input.metadata_ref else [])]
        else:
            refs = [self.input.query_ref, self.input.criteria_ref, self.input.routing_policy_ref]
        return [*refs, *self.model_bindings.values(), *([self.selection_ref] if self.selection_ref else [])]


class UIPolicy(PipelineModel):
    schema_version: Literal[1] = 1
    policy_version: Literal["preview-1"] = "preview-1"
    resources: UIResourceLimits = Field(default_factory=UIResourceLimits)
    limits: UICallLimits = Field(default_factory=UICallLimits)


class UIEvaluationPolicy(PipelineModel):
    schema_version: Literal[1] = 1
    status: Literal["pending"] = "pending"
    dataset: Literal["development"] = "development"


UICapability = Literal[
    "ui.manual", "ui.search", "ui.normalize", "ui.ocr", "ui.analyze", "ui.layout", "ui.crop", "ui.project"
]


class UIStepBinding(PipelineModel):
    schema_version: Literal[1] = 1
    task_id: Identifier
    step_id: Identifier
    revision: int = Field(default=0, ge=0, le=2, strict=True)
    capability: UICapability
    source_id: Identifier | None = None
    inputs: list[ArtifactRef] = Field(min_length=1, max_length=32)
    parameters_ref: ArtifactRef | None = None
    dependency_hashes: dict[Identifier, Digest] = Field(default_factory=dict)
    outputs: list[ArtifactRef] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def consistent_scope(self):
        refs = [*self.inputs, *self.outputs, *([self.parameters_ref] if self.parameters_ref else [])]
        if any(ref.task_id != self.task_id for ref in refs):
            raise ValueError("Step references must belong to the task")
        return self

    @property
    def input_hash(self) -> str:
        return digest(self.model_dump(mode="json", exclude={"outputs"}))


class UIObservation(PipelineModel):
    state: Literal["running", "succeeded", "failed", "unknown"]
    actual: Cost | None  # Absence of a measured cost must never become a zero settlement.
    result_ref: ArtifactRef | None = None


Coordinate = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class UIBBox(UIContent):
    xyxy: tuple[Coordinate, Coordinate, Coordinate, Coordinate]

    @model_validator(mode="after")
    def positive_area(self):
        x1, y1, x2, y2 = self.xyxy
        if x2 <= x1 or y2 <= y1:
            raise ValueError("Box requires positive area")
        return self


class CanonicalImage(PipelineModel):
    schema_version: Literal[1] = 1
    source_id: Identifier
    original_ref: ArtifactRef
    canonical_ref: ArtifactRef
    width: int = Field(gt=0, le=8192, strict=True)
    height: int = Field(gt=0, le=8192, strict=True)
    original_orientation: int = Field(default=1, ge=1, le=8, strict=True)
    color_space: Literal["sRGB"] = "sRGB"
    alpha_rule: Literal["preserve"] = "preserve"

    @model_validator(mode="after")
    def valid_canonical(self):
        if self.width * self.height > 32_000_000:
            raise ValueError("Canonical exceeds pixel limit")
        if self.original_ref.task_id != self.canonical_ref.task_id or self.canonical_ref.role != "canonical":
            raise ValueError("Canonical role and task scope must match")
        return self


class UIAsset(PipelineModel):
    artifact_ref: ArtifactRef
    element_id: Identifier | None = None
    role: Literal["original", "canonical", "layout", "texts", "rect_crop", "overlay", "quality_report"]
    epistemic_status: Literal["observed", "estimated", "hypothesis"]
    source_refs: list[ArtifactRef] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def consistent_asset(self):
        if self.artifact_ref.role != self.role:
            raise ValueError("Asset role must match its stored reference")
        if any(ref.task_id != self.artifact_ref.task_id for ref in self.source_refs):
            raise ValueError("Asset sources must belong to the task")
        return self


class ImageView(PipelineModel):
    schema_version: Literal[1] = 1
    view_id: Identifier
    kind: Literal["overview", "ocr_tile", "local"]
    canonical_ref: ArtifactRef
    input_ref: ArtifactRef
    crop: tuple[int, int, int, int]
    width: int = Field(gt=0, le=8192, strict=True)
    height: int = Field(gt=0, le=8192, strict=True)
    orientation: Literal[1] = 1
    forward: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
    inverse: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]

    @model_validator(mode="after")
    def valid_transform(self):
        import numpy as np

        x1, y1, x2, y2 = self.crop
        if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
            raise ValueError("Invalid view crop")
        if self.canonical_ref.task_id != self.input_ref.task_id:
            raise ValueError("View references cross task scope")
        if not np.allclose(np.asarray(self.forward) @ np.asarray(self.inverse), np.eye(3), atol=1e-9):
            raise ValueError("View transforms must be inverse")
        return self
