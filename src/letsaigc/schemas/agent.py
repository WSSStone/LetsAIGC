from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictAgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BackendName(StrEnum):
    auto = "auto"
    comfy = "comfy"
    openai = "openai"


class GenerationIntent(StrEnum):
    text_to_image = "text_to_image"
    image_to_image = "image_to_image"
    text_to_video = "text_to_video"
    image_to_video = "image_to_video"
    sprite_sequence = "sprite_sequence"
    short_drama = "short_drama"


class AgentTaskState(StrEnum):
    intake = "intake"
    resolve_assets = "resolve_assets"
    interpret_intent = "interpret_intent"
    inspect_capabilities = "inspect_capabilities"
    draft_plan = "draft_plan"
    validate_budget = "validate_budget"
    awaiting_approval = "awaiting_approval"
    execute = "execute"
    normalize_output = "normalize_output"
    evaluate = "evaluate"
    accepted = "accepted"
    revise = "revise"
    finalize = "finalize"
    failed = "failed"
    rejected = "rejected"


class ResolvedAsset(StrictAgentModel):
    id: str
    source_type: Literal["local", "https"]
    safe_source: str
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    size_bytes: int = Field(gt=0, le=25 * 1024 * 1024)
    local_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    derived_path: str
    derived_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class BackendCapability(StrictAgentModel):
    backend: Literal["comfy", "openai"]
    intents: list[GenerationIntent]
    media_kinds: list[Literal["image", "video"]]
    supports_alpha: bool
    sizes: list[str] = Field(default_factory=list)
    stability: Literal["stable", "experimental"] = "stable"
    cost_type: Literal["local_gpu", "remote_usd"]
    available: bool = True
    reason: str | None = None


class TaskBudget(StrictAgentModel):
    max_total_cost_usd: float = Field(ge=0)
    max_iteration_cost_usd: float = Field(ge=0)
    max_total_gpu_minutes: float = Field(ge=0)
    max_iteration_gpu_minutes: float = Field(ge=0)
    max_revisions: int = Field(default=10, ge=0, le=10)

    @model_validator(mode="after")
    def iteration_not_greater_than_total(self):
        if self.max_iteration_cost_usd > self.max_total_cost_usd:
            raise ValueError("per-iteration cost budget exceeds total cost budget")
        if self.max_iteration_gpu_minutes > self.max_total_gpu_minutes:
            raise ValueError("per-iteration GPU budget exceeds total GPU budget")
        return self


class ExecutionEnvelope(StrictAgentModel):
    backend: Literal["comfy", "openai"]
    model: str
    recipe: str | None = None
    output_count: Literal[1] = 1
    max_width: int = Field(gt=0)
    max_height: int = Field(gt=0)
    quality: Literal["low", "medium", "high"] | None = None
    background: Literal["auto", "opaque", "transparent"] | None = None
    max_steps: int | None = Field(default=None, gt=0)
    allowed_tools: list[str] = Field(min_length=1, max_length=1)
    mutable_parameters: list[str] = Field(default_factory=list)
    budget: TaskBudget

    @field_validator("mutable_parameters")
    @classmethod
    def safe_mutables(cls, values: list[str]) -> list[str]:
        if any(not value or value.startswith("_") for value in values):
            raise ValueError("invalid mutable parameter")
        return sorted(set(values))


class GenerationPlan(StrictAgentModel):
    schema_version: Literal[1] = 1
    task_id: str
    session_id: str
    intent: GenerationIntent
    user_intent: str = Field(min_length=1)
    backend: Literal["comfy", "openai"]
    model: str
    recipe: str | None = None
    agent_model: str = "gpt-5.6-luna"
    dependency_hashes: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any]
    input_assets: list[ResolvedAsset] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)
    estimated_iteration_cost_usd: float = Field(default=0, ge=0)
    estimated_iteration_gpu_minutes: float = Field(default=0, ge=0)
    envelope: ExecutionEnvelope

    @model_validator(mode="after")
    def envelope_matches_plan(self):
        if (self.backend, self.model, self.recipe) != (
            self.envelope.backend,
            self.envelope.model,
            self.envelope.recipe,
        ):
            raise ValueError("plan and execution envelope disagree")
        return self


class ApprovalRecord(StrictAgentModel):
    plan_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    envelope: ExecutionEnvelope


class BudgetUsage(StrictAgentModel):
    reserved_cost_usd: float = Field(default=0, ge=0)
    actual_cost_usd: float = Field(default=0, ge=0)
    reserved_gpu_minutes: float = Field(default=0, ge=0)
    actual_gpu_minutes: float = Field(default=0, ge=0)
    unsettled_cost_usd: float = Field(default=0, ge=0)
    unsettled_gpu_minutes: float = Field(default=0, ge=0)


class AgentEvaluation(StrictAgentModel):
    hard_constraints: dict[str, bool]
    score: float = Field(ge=0, le=1)
    issues: list[str] = Field(default_factory=list)
    revision: dict[str, Any] = Field(default_factory=dict)
    stop_reason: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    cost_usd: float = Field(default=0, ge=0)

    @property
    def accepted(self) -> bool:
        return bool(self.hard_constraints) and all(self.hard_constraints.values()) and self.score >= 0.8


class AgentCandidate(StrictAgentModel):
    iteration_id: str
    run_id: str | None = None
    outputs: list[str]
    output_hashes: list[str] = Field(default_factory=list)
    evaluation: AgentEvaluation | None = None


class AgentIteration(StrictAgentModel):
    id: str
    index: int = Field(ge=0, le=10)
    parameters: dict[str, Any]
    tool_name: str
    tool_arguments_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    candidate: AgentCandidate | None = None
    error: dict[str, Any] | None = None


class AgentTaskRecord(StrictAgentModel):
    schema_version: Literal[1] = 1
    id: str
    session_id: str
    state: AgentTaskState
    state_history: list[AgentTaskState] = Field(default_factory=lambda: [AgentTaskState.intake])
    run_id: str | None = None
    plan: GenerationPlan | None = None
    approval: ApprovalRecord | None = None
    budget_usage: BudgetUsage = Field(default_factory=BudgetUsage)
    iterations: list[AgentIteration] = Field(default_factory=list)
    best_candidate: AgentCandidate | None = None
    final_candidate: AgentCandidate | None = None
    stop_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentTurn(StrictAgentModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    task_id: str | None = None


class AgentSession(StrictAgentModel):
    schema_version: Literal[1] = 1
    id: str
    turns: list[AgentTurn] = Field(default_factory=list)
    current_task_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProviderModelEntry(StrictAgentModel):
    id: str
    provider: Literal["openai"]
    snapshot: str
    intents: list[GenerationIntent]
    terms_id: str
    license_lane: Literal["production", "conditional", "restricted"]
    pricing_id: str
    credential_env: str


class ProviderCatalog(StrictAgentModel):
    schema_version: Literal[1] = 1
    models: list[ProviderModelEntry]


class WorkflowRecipe(StrictAgentModel):
    schema_version: Literal[1] = 1
    id: str
    version: str
    intent: GenerationIntent
    stability: Literal["stable", "experimental"]
    base_workflow: str
    models: list[str]
    allowed_nodes: list[str]
    parameter_bounds: dict[str, dict[str, Any]]
    mutable_parameters: list[str]
    outputs: list[dict[str, Any]]
    resource_budget: dict[str, Any]


class CompiledWorkflow(StrictAgentModel):
    recipe_id: str
    recipe_version: str
    graph: dict[str, Any]
    graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    models: list[str]
    outputs: list[dict[str, Any]]
    local_graph_path: str
    local_contract_path: str
    contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    validation: dict[str, bool] = Field(default_factory=dict)
