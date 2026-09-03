"""Versioned domain contracts. No Temporal SDK or filesystem dependencies."""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent import TaskBudget

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
MAX_PAYLOAD_BYTES = 64 * 1024


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_payload(value: Any) -> None:
    """Reject transport secrets and local paths before entering history."""
    payload = json.loads(canonical_json(value))
    if len(canonical_json(payload).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("Pipeline payload exceeds 64 KiB; use artifact references")

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key.lower() in {"api_key", "token", "authorization", "password", "secret", "credentials"}:
                    raise ValueError("Credential fields cannot enter pipeline history")
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            if re.match(r"^(?:[a-zA-Z]:[/\\]|/|\\\\)", item):
                raise ValueError("Absolute paths cannot enter pipeline history")
            for match in re.findall(r"https?://[^\s\"<>]+", item):
                parsed = urlsplit(match)
                if parsed.query or parsed.fragment or parsed.username or parsed.password:
                    raise ValueError("Sensitive URL components cannot enter pipeline history")

    walk(payload)


class PipelineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)

    @model_validator(mode="after")
    def transport_safe(self):
        validate_payload(self)
        return self


class ArtifactRef(PipelineModel):
    schema_version: Literal[1] = 1
    task_id: Identifier
    artifact_id: Identifier
    key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_./-]+$")
    sha256: Digest
    size_bytes: int = Field(ge=0)
    media_type: str
    role: Identifier
    operation_id: Identifier
    source_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def safe_key(self):
        if any(part in {"", ".", ".."} for part in self.key.split("/")):
            raise ValueError("Unsafe artifact key")
        if not self.key.startswith(self.task_id + "/"):
            raise ValueError("Artifact key is outside task scope")
        return self


class PipelineState(StrEnum):
    planned = "planned"
    running = "running"
    awaiting_approval = "awaiting_approval"
    awaiting_reconciliation = "awaiting_reconciliation"
    succeeded = "succeeded"
    failed = "failed"
    cancel_requested = "cancel_requested"
    cancelled = "cancelled"
    rejected = "rejected"


class Cost(PipelineModel):
    cost_usd: float = Field(default=0, ge=0)
    gpu_minutes: float = Field(default=0, ge=0)


class ApprovalEnvelope(PipelineModel):
    stage: Literal["analysis", "generation"] = "generation"
    allowed_capabilities: list[Identifier] = Field(min_length=1, max_length=16)
    budget: TaskBudget
    mutable_parameters: list[Literal["prompt", "negative_prompt", "seed"]] = Field(default_factory=list)


class PipelinePlan(PipelineModel):
    schema_version: Literal[1] = 1
    task_id: Identifier
    workflow_type: Identifier
    workflow_version: Literal["1"] = "1"
    executor: Literal["temporal"] = "temporal"
    inputs: list[ArtifactRef] = Field(default_factory=list, max_length=32)
    generation_plan: ArtifactRef | None = None
    envelope: ApprovalEnvelope
    estimated_iteration: Cost = Field(default_factory=Cost)
    parameters: dict[str, Any] = Field(default_factory=dict)
    dependency_hashes: dict[str, Digest] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_plan(self):
        validate_payload(self)
        refs = [*self.inputs, *([self.generation_plan] if self.generation_plan else [])]
        if any(ref.task_id != self.task_id for ref in refs):
            raise ValueError("Input references must belong to this task")
        if len(set(self.envelope.allowed_capabilities)) != len(self.envelope.allowed_capabilities):
            raise ValueError("Duplicate capability")
        budget = self.envelope.budget
        if (
            self.estimated_iteration.cost_usd > budget.max_iteration_cost_usd
            or self.estimated_iteration.gpu_minutes > budget.max_iteration_gpu_minutes
        ):
            raise ValueError("Estimated operation exceeds iteration budget")
        return self

    @property
    def fingerprint(self) -> str:
        return digest(self)


class ApprovalRequest(PipelineModel):
    request_id: Identifier
    task_id: Identifier
    plan_fingerprint: Digest
    decision: Literal["approve", "reject"]


class StepRun(PipelineModel):
    step_id: Identifier
    operation_id: Identifier
    revision: int = Field(ge=0)
    state: str
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    error_code: str | None = None


class PipelineRun(PipelineModel):
    schema_version: Literal[1] = 1
    task_id: Identifier
    workflow_id: str
    temporal_run_id: str
    workflow_version: str = "1"
    plan_fingerprint: Digest
    state: PipelineState = PipelineState.planned
    revision: int = Field(default=0, ge=0)
    steps: list[StepRun] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    stop_reason: str | None = None
    projection_sequence: int = Field(default=0, ge=0)


class OperationRecord(PipelineModel):
    operation_id: Identifier
    task_id: Identifier
    step_id: Identifier
    revision: int = Field(ge=0)
    input_hash: Digest
    state: Literal["prepared", "submitting", "submitted", "running", "succeeded", "failed", "outcome_unknown"]
    provider_request_id: str | None = None
    reserved: Cost = Field(default_factory=Cost)
    actual: Cost = Field(default_factory=Cost)
    result: dict[str, Any] = Field(default_factory=dict)


def operation_id(plan: PipelinePlan, step_id: str, revision: int) -> str:
    return (
        "op-"
        + digest(
            {
                "task": plan.task_id,
                "step": step_id,
                "revision": revision,
                "plan": plan.fingerprint,
            }
        )[:48]
    )
