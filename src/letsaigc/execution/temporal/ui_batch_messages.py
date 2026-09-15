"""Reference-only batch transport; image bodies remain in ArtifactStore."""

from ...schemas.pipeline import ApprovalRequest, PipelineRun
from .messages import ActivityInput, WorkflowInput


class UIBatchWorkflowInput(WorkflowInput):
    initial_approval: ApprovalRequest | None = None


class UIBatchActivityInput(ActivityInput):
    child_run: PipelineRun | None = None
