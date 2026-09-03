from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LicenseLane(StrEnum):
    production = "production"
    conditional = "conditional"
    restricted = "restricted"


class ModelSource(StrictModel):
    repo: str
    revision: str = Field(pattern=r"^[a-f0-9]{40}$")


class ModelFile(StrictModel):
    source_path: str
    target_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0)

    @field_validator("source_path", "target_path")
    @classmethod
    def relative_safe_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("model paths must be relative and may not traverse parents")
        return value.replace("\\", "/")


class ModelLicense(StrictModel):
    id: str
    url: str
    lane: LicenseLane
    commercial_use: str


class ModelResources(StrictModel):
    min_vram_gib: float = Field(ge=0)
    recommended_vram_gib: float = Field(ge=0)
    ram_gib: float = Field(ge=0)
    disk_gib: float = Field(ge=0)


class ModelEntry(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    family: str
    title: str
    source: ModelSource
    files: list[ModelFile] = Field(min_length=1)
    format: str
    license: ModelLicense
    gated: bool
    profiles: list[str] = Field(min_length=1)
    resources: ModelResources
    enabled_by_default: bool
    runtime_status: Literal["qualified", "experimental", "catalog_only"] = "qualified"


class Catalog(StrictModel):
    schema_version: Literal[1]
    profiles: dict[str, list[str]]
    models: list[ModelEntry]

    def by_id(self) -> dict[str, ModelEntry]:
        return {model.id: model for model in self.models}


class LicenseLaneRules(StrictModel):
    allow_inference: bool
    allow_training: bool
    allow_production_export: bool
    requires_attestation: bool


class ProductionExportRules(StrictModel):
    require_status: Literal["succeeded"]
    require_hashes: bool
    require_contract_validation: bool
    require_provenance: bool
    require_human_approval: bool
    allowed_lanes: list[LicenseLane] = Field(min_length=1)


class LicensePolicy(StrictModel):
    schema_version: Literal[1]
    safe_weight_extensions: list[str] = Field(min_length=1)
    deny_unknown_license: bool
    lanes: dict[LicenseLane, LicenseLaneRules]
    production_export: ProductionExportRules


class ResourceBudget(StrictModel):
    vram_gib: float = Field(ge=0)
    ram_gib: float = Field(ge=0)
    free_disk_gib: float = Field(ge=0)
    timeout_seconds: int = Field(gt=0)


class TemporalBudget(StrictModel):
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    fps: float | None = Field(default=None, gt=0)
    frame_count: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    temporary_disk_gib: float | None = Field(default=None, gt=0)


class MediaOutputDeclaration(StrictModel):
    node_id: str = Field(min_length=1)
    history_field: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$")
    role: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    media_kind: Literal["image", "video", "audio", "subtitle", "frames", "metadata"]
    allowed_extensions: list[str] = Field(min_length=1)
    required: bool = True

    @field_validator("allowed_extensions")
    @classmethod
    def safe_extensions(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            extension = value.lower()
            if not extension.startswith(".") or any(char in extension for char in "/\\"):
                raise ValueError("media extensions must be simple dot-prefixed values")
            normalized.append(extension)
        return normalized


class MediaMetadata(StrictModel):
    mime_type: str
    codec: str | None = None
    pixel_format: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    frame_count: int | None = Field(default=None, ge=0)
    fps: float | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    has_alpha: bool | None = None
    has_audio: bool = False


class SourceArtifact(StrictModel):
    run_id: str | None = None
    role: str | None = None
    path: str | None = None
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class VideoImportMetadata(StrictModel):
    schema_version: Literal[1] = 1
    source: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    license_id: str = Field(min_length=1)
    license_lane: LicenseLane
    commercial_use: str = Field(min_length=1)
    runpack_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    notes: str | None = None


class VideoJobReference(StrictModel):
    logical_name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExpectedMediaOutput(StrictModel):
    media_kind: Literal["video"] = "video"
    allowed_extensions: list[str] = Field(min_length=1)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    max_frames: int | None = Field(default=None, gt=0)

    @field_validator("allowed_extensions")
    @classmethod
    def safe_extensions(cls, values: list[str]) -> list[str]:
        return MediaOutputDeclaration.safe_extensions(values)


class VideoJob(StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    workflow: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    inputs: dict[str, Any]
    models: list[str] = Field(min_length=1)
    references: list[VideoJobReference] = Field(default_factory=list)
    resource_budget: ResourceBudget
    expected_output: ExpectedMediaOutput


class ChromaKeyConfig(StrictModel):
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    transparent_delta: float = Field(ge=0)
    opaque_delta: float = Field(gt=0)
    despill_strength: float = Field(ge=0, le=1)
    edge_feather_px: float = Field(ge=0, le=8)
    hard_alpha: bool = False


class AnchorConfig(StrictModel):
    mode: Literal["alpha_bottom_center", "fixed", "manual"]
    target_x: float = Field(ge=0, le=1)
    target_y: float = Field(ge=0, le=1)
    max_raw_drift_fraction: float = Field(ge=0, le=1)
    max_error_px: float = Field(ge=0)


class SpriteValidationConfig(StrictModel):
    min_foreground_fraction: float = Field(ge=0, le=1)
    max_foreground_fraction: float = Field(ge=0, le=1)
    max_border_contact_fraction: float = Field(ge=0, le=1)


class SpriteAtlasConfig(StrictModel):
    max_width: int = Field(gt=0)
    max_height: int = Field(gt=0)
    layout: Literal["near_square_row_major"] = "near_square_row_major"


class SpriteProfile(StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    canvas_width: int = Field(gt=0)
    canvas_height: int = Field(gt=0)
    fps: float = Field(gt=0)
    max_frames: int = Field(gt=0)
    margin_fraction: float = Field(ge=0, lt=0.5)
    pixel_art: bool = False
    background: ChromaKeyConfig
    anchor: AnchorConfig
    validation: SpriteValidationConfig
    atlas: SpriteAtlasConfig


class SpriteRect(StrictModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class SpriteAnchor(StrictModel):
    x: float
    y: float
    normalized_x: float
    normalized_y: float


class SpriteFrameRecord(StrictModel):
    index: int = Field(ge=0)
    filename: str
    rect: SpriteRect
    duration_ms: float = Field(gt=0)
    anchor: SpriteAnchor
    raw_anchor: SpriteAnchor
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    foreground_fraction: float = Field(ge=0, le=1)
    border_contact_fraction: float = Field(ge=0, le=1)
    raw_border_contact_fraction: float = Field(default=0, ge=0, le=1)


class SpriteSheetMetadata(StrictModel):
    schema_version: Literal[1] = 1
    profile_id: str
    sheet_filename: str
    sheet_width: int = Field(gt=0)
    sheet_height: int = Field(gt=0)
    columns: int = Field(gt=0)
    rows: int = Field(gt=0)
    source_run_id: str | None = None
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    frames: list[SpriteFrameRecord] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)
    validations: dict[str, bool]


class DramaShot(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    source_run_id: str | None = None
    workflow: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    transition: Literal["cut", "fade"] = "cut"
    fade_seconds: float = Field(default=0.5, gt=0)
    expected_duration_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def exactly_one_source(self):
        if bool(self.source_run_id) == bool(self.workflow):
            raise ValueError("drama shot requires exactly one source_run_id or workflow")
        return self


class DramaProject(StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    shots: list[DramaShot] = Field(min_length=1)
    character_references: dict[str, str] = Field(default_factory=dict)
    style_references: list[str] = Field(default_factory=list)
    audio_path: str | None = None
    subtitle_path: str | None = None
    burn_subtitles: bool = False

    @model_validator(mode="after")
    def unique_shots(self):
        ids = [shot.id for shot in self.shots]
        if len(ids) != len(set(ids)):
            raise ValueError("drama shot ids must be unique")
        return self


class TrainingResourceBudget(StrictModel):
    vram_gib: float = Field(gt=0)
    ram_gib: float = Field(gt=0)
    free_disk_gib: float = Field(gt=0)
    timeout_minutes: int = Field(gt=0)


class TrainingConfig(StrictModel):
    schema_version: Literal[1]
    id: str
    base_model: str
    dataset_dir: str
    output_dir: str
    output_name: str
    caption_extension: str
    resolution: int = Field(ge=256)
    enable_bucket: bool = False
    min_bucket_reso: int = Field(ge=256)
    max_bucket_reso: int = Field(ge=256)
    max_train_steps: int = Field(gt=0)
    train_batch_size: Literal[1]
    network_module: str
    network_dim: int = Field(gt=0)
    network_alpha: int = Field(gt=0)
    learning_rate: float = Field(gt=0)
    unet_lr: float = Field(gt=0)
    train_text_encoder: bool
    mixed_precision: Literal["fp16", "bf16"]
    save_precision: Literal["float", "fp16", "bf16"]
    optimizer_type: str
    gradient_checkpointing: bool
    sdpa: bool
    cache_latents: bool
    cache_latents_to_disk: bool
    cache_text_encoder_outputs: bool
    cache_text_encoder_outputs_to_disk: bool
    no_half_vae: bool
    seed: int = Field(ge=0)
    save_model_as: Literal["safetensors"]
    max_data_loader_n_workers: int = Field(ge=0)
    resource_budget: TrainingResourceBudget


class EvaluationCase(StrictModel):
    id: str
    prompt: str = Field(min_length=1)


class EvaluationSuite(StrictModel):
    schema_version: Literal[1]
    id: str
    workflow: str
    fixed_seed: int = Field(ge=0)
    cases: list[EvaluationCase] = Field(min_length=1)
    metrics: list[str] = Field(min_length=1)
    human_review: list[str] = Field(min_length=1)


class AdapterDependency(StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    record: str

    @field_validator("path", "record")
    @classmethod
    def relative_safe_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("adapter dependency paths must be relative and safe")
        return value.replace("\\", "/")


class WorkflowContract(StrictModel):
    schema_version: Literal[1]
    id: str
    version: str
    description: str
    ui_workflow: str
    api_workflow: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    bindings: dict[str, str]
    defaults: dict[str, Any]
    models: list[str]
    adapters: list[AdapterDependency] = Field(default_factory=list)
    nodes: list[str]
    resource_budget: ResourceBudget
    export_lanes: list[LicenseLane]
    media_kind: Literal["image", "video"] = "image"
    outputs: list[MediaOutputDeclaration] = Field(default_factory=list)
    temporal_budget: TemporalBudget | None = None


class RunOutput(StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int | None = Field(default=None, ge=0)
    role: str | None = None
    media_kind: Literal["image", "video", "audio", "subtitle", "frames", "metadata"] | None = None
    media: MediaMetadata | None = None
    derived_from_run_id: str | None = None
    derived_from_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class RunGovernance(StrictModel):
    license_lanes: list[LicenseLane]
    validations: dict[str, bool]
    human_approved: bool = False
    human_review: dict[str, Any] | None = None


class AgentRunMetadata(StrictModel):
    session_id: str
    task_id: str
    iteration_id: str | None = None
    approval_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    provider_request_id: str | None = None
    model_snapshot: str | None = None
    redacted_request_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    input_asset_sha256: list[str] = Field(default_factory=list)
    budget_usage: dict[str, Any] = Field(default_factory=dict)
    critic: dict[str, Any] | None = None
    stop_reason: str | None = None


class RunManifest(StrictModel):
    schema_version: Literal["1.0", "1.1", "1.2"] = "1.2"
    run_id: str
    kind: Literal[
        "inference",
        "evaluation",
        "training",
        "video_generation",
        "sprite_pipeline",
        "drama_render",
        "agent_task",
        "remote_image_generation",
    ]
    status: Literal["created", "validated", "queued", "running", "succeeded", "failed", "cancelled"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: dict[str, Any]
    parameters: dict[str, Any]
    environment: dict[str, Any]
    outputs: list[RunOutput] = Field(default_factory=list)
    parent_run_id: str | None = None
    source_artifacts: list[SourceArtifact] = Field(default_factory=list)
    tracking: dict[str, Any] = Field(default_factory=dict)
    governance: RunGovernance
    agent: AgentRunMetadata | None = None
    error: dict[str, Any] | None = None


class AdapterRecord(StrictModel):
    schema_version: Literal[1] = 1
    id: str
    base_model_id: str
    base_model_sha256: list[str] = Field(min_length=1)
    adapter_path: str
    adapter_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_path: str
    dataset_dvc_rev: str | None
    training_config_path: str
    training_config_sha256: str
    effective_parameters: dict[str, Any]
    trainer_revision: str
    mlflow_run_id: str | None = None
    evaluations: list[dict[str, Any]] = Field(default_factory=list)
    license_lane: LicenseLane
    promotion_status: Literal["candidate", "evaluated", "approved", "rejected"] = "candidate"
    approval: dict[str, Any] | None = None
