from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class RunOutput(StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int | None = Field(default=None, ge=0)


class RunGovernance(StrictModel):
    license_lanes: list[LicenseLane]
    validations: dict[str, bool]
    human_approved: bool = False
    human_review: dict[str, Any] | None = None


class RunManifest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    kind: Literal["inference", "evaluation", "training"]
    status: Literal["created", "validated", "queued", "running", "succeeded", "failed", "cancelled"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: dict[str, Any]
    parameters: dict[str, Any]
    environment: dict[str, Any]
    outputs: list[RunOutput] = Field(default_factory=list)
    tracking: dict[str, Any] = Field(default_factory=dict)
    governance: RunGovernance
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
