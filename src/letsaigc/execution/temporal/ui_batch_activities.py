"""Trusted batch adapters; shared ledger controls every admission."""

from temporalio import activity

from ...pipelines.errors import PipelineError
from ...ui_analysis.batch import BatchExecution, BatchPreparation
from .activities import PipelineActivities
from .projection import project
from .ui_batch_messages import UIBatchActivityInput


class UIBatchActivities(PipelineActivities):
    def __init__(self, service):
        super().__init__(service)
        self.execution = BatchExecution(service)

    @activity.defn(name="ui.batch.prepare.v1")
    def prepare(self, argument: UIBatchActivityInput) -> BatchPreparation:
        def prepare(plan):
            result = self.execution.prepare(plan)
            if result.child is None and result.state in {"succeeded", "partial", "failed", "cancelled"}:
                result = result.model_copy(update={"artifacts": [self.execution.manifest(plan)]})
            return result
        return self.invoke(argument, prepare)

    @activity.defn(name="ui.batch.complete.v1")
    def complete(self, argument: UIBatchActivityInput) -> BatchPreparation:
        if argument.child_run is None:
            raise PipelineError("invalid_child_result")
        return self.invoke(argument, lambda plan: self.execution.complete(plan, argument.child_run))

    @activity.defn(name="ui.batch.status.v1")
    def status(self, argument: UIBatchActivityInput) -> BatchPreparation:
        return self.invoke(argument, self.execution.status)

    @activity.defn(name="ui.batch.cancel.v1")
    def cancel(self, argument: UIBatchActivityInput) -> bool:
        return self.invoke(argument, self.execution.cancel, verify_inputs=False)

    @activity.defn(name="ui.batch.checkpoint.v1")
    def checkpoint(self, argument: UIBatchActivityInput) -> BatchPreparation:
        def checkpoint(plan):
            result = self.execution.checkpoint(plan)
            if result.state in {"succeeded", "partial"}:
                result = result.model_copy(update={"artifacts": [self.execution.manifest(plan)]})
            return result
        return self.invoke(argument, checkpoint)

    @activity.defn(name="ui.batch.project.v1")
    def project(self, argument: UIBatchActivityInput) -> bool:
        def write(plan):
            if argument.run is None or argument.run.task_id != plan.task_id:
                raise PipelineError("missing_projection")
            project(self.service, argument.run)
            return True
        return self.invoke(argument, write, verify_inputs=False)

    def registered(self):
        return [self.prepare, self.complete, self.status, self.cancel, self.checkpoint, self.project]
