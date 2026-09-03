from typing import Any, Literal, Protocol

from pydantic import Field

from ..schemas.pipeline import Cost, Identifier, PipelineModel


class Capability(PipelineModel):
    id: Identifier
    version: str = "1"
    idempotent_submission: bool = False
    can_inspect: bool = True
    can_cancel: bool = False
    resource: Identifier | None = None
    timeout_seconds: int = Field(default=600, gt=0, le=86400)


class Submission(PipelineModel):
    request_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Observation(PipelineModel):
    state: Literal["running", "succeeded", "failed", "unknown"]
    actual: Cost = Field(default_factory=Cost)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperationBackend(Protocol):
    capability: Capability

    def submit(self, operation_id: str, arguments: dict[str, Any]) -> Submission: ...
    def inspect(self, submission: Submission) -> Observation: ...
    def collect(self, submission: Submission) -> list[tuple[str, bytes, str]]: ...
    def cancel(self, submission: Submission) -> Observation: ...
