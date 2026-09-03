"""Trusted Activity registration. Provider exceptions never enter workflow history."""

from __future__ import annotations

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ...pipelines.errors import PipelineError
from ...pipelines.service import PipelineService
from ...schemas.pipeline import OperationRecord, operation_id, validate_payload
from .messages import ActivityInput
from .projection import project


class PipelineActivities:
    def __init__(self, service: PipelineService) -> None:
        self.service = service

    def invoke(self, argument: ActivityInput, function, *, verify_inputs: bool = True):
        try:
            validate_payload(argument)
            activity.heartbeat("entered")
            plan = self.service.checked_plan(argument.task_id, argument.plan_fingerprint, verify_inputs=verify_inputs)
            result = function(plan)
            validate_payload(result)
            activity.heartbeat("completed")
            return result
        except PipelineError as exc:
            raise ApplicationError(exc.code, type=exc.code, non_retryable=True) from None
        except Exception:
            # No chained provider exception, request body, path or transport URL.
            raise ApplicationError("activity_dependency_failure", type="dependency_failure") from None

    @activity.defn(name="pipeline.validate.v1")
    def validate(self, argument: ActivityInput) -> bool:
        return self.invoke(argument, lambda plan: True)

    @activity.defn(name="pipeline.approval.v1")
    def approval(self, argument: ActivityInput) -> bool:
        return self.invoke(argument, lambda plan: self.service.ledger.consume_approval(argument.approval))

    @activity.defn(name="pipeline.submit.v1")
    def submit(self, argument: ActivityInput) -> OperationRecord:
        return self.invoke(argument, lambda plan: self.service.submit(plan, argument.revision), verify_inputs=False)

    @activity.defn(name="pipeline.observe.v1")
    def observe(self, argument: ActivityInput) -> OperationRecord:
        return self.invoke(
            argument, lambda plan: self.service.observe(plan, argument.operation_id), verify_inputs=False
        )

    @activity.defn(name="pipeline.collect.v1")
    def collect(self, argument: ActivityInput) -> OperationRecord:
        return self.invoke(
            argument, lambda plan: self.service.collect(plan, argument.operation_id), verify_inputs=False
        )

    @activity.defn(name="pipeline.cancel.v1")
    def cancel(self, argument: ActivityInput) -> bool:
        return self.invoke(argument, lambda plan: self.service.cancel(plan, argument.revision), verify_inputs=False)

    @activity.defn(name="pipeline.uncertain.v1")
    def uncertain(self, argument: ActivityInput) -> bool:
        def audit(plan):
            key = operation_id(plan, "generate", argument.revision)
            if key != argument.operation_id:
                raise PipelineError("operation_scope")
            self.service.ledger.record_interruption(key)
            return True

        return self.invoke(argument, audit, verify_inputs=False)

    @activity.defn(name="pipeline.project.v1")
    def project(self, argument: ActivityInput) -> bool:
        def write(plan):
            if argument.run is None:
                raise PipelineError("missing_projection")
            project(self.service, argument.run)
            return True

        return self.invoke(argument, write, verify_inputs=False)

    def registered(self) -> list:
        return [
            self.validate,
            self.approval,
            self.submit,
            self.observe,
            self.collect,
            self.cancel,
            self.uncertain,
            self.project,
        ]
