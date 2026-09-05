"""Trusted UI step adapters; history contains only bounded references."""

from temporalio import activity

from ...pipelines.errors import PipelineError
from ...schemas.pipeline import OperationRecord, canonical_json, operation_id
from ...ui_analysis.execution import UIExecution, UIStepPreparation
from .activities import PipelineActivities
from .projection import atomic_json
from .ui_messages import UIActivityInput


class UIActivities(PipelineActivities):
    def __init__(self, service):
        super().__init__(service)
        self.execution = UIExecution(service)

    @activity.defn(name="ui.prepare.v1")
    def prepare(self, argument: UIActivityInput) -> UIStepPreparation:
        return self.invoke(argument, lambda plan: self.execution.prepare(plan, argument.phase, argument.position))

    @activity.defn(name="ui.submit.v1")
    def submit(self, argument: UIActivityInput) -> OperationRecord:
        def submit(plan):
            step = self.execution.prepare(plan, argument.phase, argument.position)
            if step.binding is None:
                raise PipelineError("invalid_step")
            if step.binding.capability == "ui.search":
                from ...ui_providers.search import SearchAcquisition

                try:
                    previous = self.service.ledger.get(operation_id(plan, step.binding.step_id, 0))
                except PipelineError as exc:
                    if exc.code != "unknown_operation":
                        raise
                    previous = None
                if previous is None:
                    SearchAcquisition(self.service).acquire(plan)
            return self.service.submit_step(plan, step.binding, step.reservation)

        return self.invoke(argument, submit)

    @activity.defn(name="ui.observe.v1")
    def observe(self, argument: UIActivityInput) -> OperationRecord:
        return self.invoke(
            argument, lambda plan: self.service.observe_step(plan, argument.operation_id), verify_inputs=False
        )

    @activity.defn(name="ui.collect.v1")
    def collect(self, argument: UIActivityInput) -> OperationRecord:
        return self.invoke(
            argument, lambda plan: self.service.collect_step(plan, argument.operation_id), verify_inputs=False
        )

    @activity.defn(name="ui.uncertain.v1")
    def uncertain(self, argument: UIActivityInput) -> bool:
        def audit(plan):
            # An Activity timeout can precede creation of an operation. Do not
            # invent a reservation, but never clear one that might be accepted.
            try:
                record = self.service.ledger.get(argument.operation_id)
            except PipelineError as exc:
                if exc.code == "unknown_operation":
                    return True
                raise
            if (
                record.task_id != plan.task_id
                or operation_id(plan, record.step_id, record.revision) != record.operation_id
            ):
                raise PipelineError("operation_scope")
            self.service.ledger.record_interruption(record.operation_id)
            return True

        return self.invoke(argument, audit, verify_inputs=False)

    @activity.defn(name="ui.cancel.v1")
    def cancel(self, argument: UIActivityInput) -> bool:
        return self.invoke(argument, self.execution.cancel, verify_inputs=False)

    @activity.defn(name="ui.project.v1")
    def project(self, argument: UIActivityInput) -> bool:
        def write(plan):
            run = argument.run
            if run is None or run.task_id != plan.task_id or run.plan_fingerprint != plan.fingerprint:
                raise PipelineError("missing_projection")
            payload = canonical_json(run)
            with self.service.ledger.transaction() as db:
                old = db.execute("SELECT * FROM projections WHERE task_id=?", (plan.task_id,)).fetchone()
                if old and old["sequence"] > run.projection_sequence:
                    return True
                if old and old["sequence"] == run.projection_sequence and old["payload"] != payload:
                    raise PipelineError("projection_conflict")
                atomic_json(self.service.root / "tasks" / plan.task_id / "run.json", payload)
                db.execute(
                    """INSERT INTO projections(task_id,sequence,payload) VALUES(?,?,?)
                    ON CONFLICT(task_id) DO UPDATE SET sequence=excluded.sequence,payload=excluded.payload""",
                    (plan.task_id, run.projection_sequence, payload),
                )
            return True

        return self.invoke(argument, write, verify_inputs=False)

    def registered(self):
        return [self.prepare, self.submit, self.observe, self.collect, self.uncertain, self.cancel, self.project]
