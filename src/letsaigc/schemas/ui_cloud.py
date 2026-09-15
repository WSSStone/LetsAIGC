"""Frozen, opt-in cloud editing profiles. No changes to historical UI requests."""
from typing import Literal

from pydantic import Field

from .agent import TaskBudget
from .pipeline import ArtifactRef, Digest, PipelineModel


class CloudImageRetry(PipelineModel):
    """One explicit extra image attempt, including its cumulative budget grant."""

    base_task_id: str = Field(min_length=1, max_length=128)
    base_operation_id: str = Field(min_length=1, max_length=128)
    budget_before: TaskBudget
    budget_after: TaskBudget


class CloudEditPolicy(PipelineModel):
    schema_version: Literal[1] = 1
    backend: Literal["openai"] = "openai"
    model: Literal["gpt-image-2"] = "gpt-image-2"
    vlm_model: str = Field(min_length=1, max_length=128)
    endpoint_fingerprint: Digest
    pricing_ref: ArtifactRef
    instruction: str = Field(min_length=1, max_length=2000)
    reasoning: Literal["low", "high"] = "low"
    max_output_tokens: Literal[1024] = 1024
    size: Literal["1024x1024"] = "1024x1024"
    quality: Literal["medium"] = "medium"
    timeout_seconds: Literal[300] = 300
    connect_timeout_seconds: Literal[10] = 10
    write_timeout_seconds: Literal[30] = 30
    pool_timeout_seconds: Literal[10] = 10
    image_detail: Literal["high"] = "high"
    response_transport: Literal["stream"] = "stream"
    output_format: Literal["png"] = "png"
    background: Literal["opaque"] = "opaque"
    n: Literal[1] = 1
    guide_budget_usd: float = Field(default=0.01, gt=0, le=0.1)
    image_budget_usd: float = Field(default=0.10, gt=0, le=1)


class CloudEditRequest(PipelineModel):
    schema_version: Literal[1] = 1
    stage: Literal["cloud_guide", "cloud_inpaint"]
    policy: CloudEditPolicy
    image_ref: ArtifactRef
    selection_ref: ArtifactRef
    selection_revision: int = Field(ge=0, strict=True)
    prompt: str = Field(min_length=1, max_length=8000)
    # Context is a crop of the reviewed image, not a claim of pixel-exact masking.
    context: dict
    guidance_ref: ArtifactRef | None = None
    retry: CloudImageRetry | None = None
