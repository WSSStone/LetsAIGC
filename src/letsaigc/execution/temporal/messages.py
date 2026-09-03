from pydantic import Field

from ...schemas.pipeline import ApprovalRequest, Digest, Identifier, PipelineModel, PipelinePlan, PipelineRun


class WorkflowInput(PipelineModel):
    activity_timeout_seconds: int = Field(default=600, ge=1, le=86400)
    plan: PipelinePlan
    run: PipelineRun | None = None
    approved: bool = False
    poll_seconds: float = Field(default=2, gt=0, le=60)
    observations_per_run: int = Field(default=100, ge=1, le=1000)
    cancelled: bool = False


class ActivityInput(PipelineModel):
    task_id: Identifier
    plan_fingerprint: Digest
    revision: int = Field(default=0, ge=0, le=10)
    operation_id: Identifier | None = None
    approval: ApprovalRequest | None = None
    run: PipelineRun | None = None
