"""UI-only transport extensions; v1 generation messages retain their fields."""

from pydantic import Field

from ...schemas.pipeline import ApprovalRequest, ArtifactRef, PipelinePlan
from ...ui_analysis.execution import UIPhase
from .messages import ActivityInput, WorkflowInput


class UIWorkflowInput(WorkflowInput):
    initial_approval: ApprovalRequest | None = None
    phase_index: int = Field(default=0, ge=0, le=7, strict=True)
    position: int = Field(default=0, ge=0, le=63, strict=True)
    active_seconds: float = Field(default=0, ge=0)
    active_limit_seconds: int = Field(default=3600, ge=1, le=3600, strict=True)
    resource_expired: bool = False


class UIActivityInput(ActivityInput):
    phase: UIPhase = "provide"
    position: int = Field(default=0, ge=0, le=63, strict=True)


class UIEditingWorkflowInput(UIWorkflowInput):
    child: PipelinePlan | None = None
    child_approved: bool = False
    initial_child_approval: ApprovalRequest | None = None
    revision_ref: ArtifactRef | None = None


class UIEditingActivityInput(ActivityInput):
    child: PipelinePlan | None = None
    revision_ref: ArtifactRef | None = None
