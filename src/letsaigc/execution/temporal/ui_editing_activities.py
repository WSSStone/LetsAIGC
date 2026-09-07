"""Editing activities retain root ownership while executing exact child plans."""

from temporalio import activity

from ...pipelines.errors import PipelineError
from ...schemas.pipeline import Cost, OperationRecord, operation_id
from ...ui_analysis.editing import EditingExecution, EditingPreparation
from .ui_activities import UIActivities
from .ui_messages import UIEditingActivityInput


class UIEditingActivities(UIActivities):
    def __init__(self, service):
        super().__init__(service)
        self.editing = EditingExecution(service)

    def child_plan(self, argument, root, *, current=False):
        supplied = argument.child
        if supplied is None:
            raise PipelineError("unknown_child")
        child = self.service.checked_plan(supplied.task_id, supplied.fingerprint, verify_inputs=current)
        if child != supplied or self.editing.child_root(child) != root.task_id:
            raise PipelineError("operation_scope")
        if current:
            self.editing.validate_current(child)
        return child

    @activity.defn(name="ui.edit.prepare.v1")
    def edit_prepare(self, argument: UIEditingActivityInput) -> EditingPreparation:
        if argument.revision_ref is not None:
            from ...ui_analysis.revision_execution import RevisionExecution

            return self.invoke(
                argument, lambda root: RevisionExecution(self.service).prepare(root, argument.revision_ref)
            )
        return self.invoke(argument, self.editing.prepare)

    @activity.defn(name="ui.edit.approval.v1")
    def edit_approval(self, argument: UIEditingActivityInput) -> bool:
        def consume(root):
            child = self.child_plan(argument, root, current=True)
            request = argument.approval
            if request is None or (request.task_id, request.plan_fingerprint) != (child.task_id, child.fingerprint):
                raise PipelineError("approval_mismatch")
            return self.service.ledger.consume_approval(request)

        return self.invoke(argument, consume)

    @activity.defn(name="ui.edit.submit.v1")
    def edit_submit(self, argument: UIEditingActivityInput) -> OperationRecord:
        def submit(root):
            child = self.child_plan(argument, root)
            binding = self.editing.binding(child)
            try:
                old = self.service.ledger.get(operation_id(child, binding.step_id, binding.revision))
            except PipelineError as exc:
                if exc.code != "unknown_operation":
                    raise
                old = None
            if old is not None and old.state != "prepared":
                return self.service.recover_step(child, old.operation_id)
            self.editing.validate_current(child)
            return self.service.submit_step(
                child,
                binding,
                Cost(
                    cost_usd=child.envelope.budget.max_iteration_cost_usd,
                    gpu_minutes=child.envelope.budget.max_iteration_gpu_minutes,
                ),
            )

        return self.invoke(argument, submit, verify_inputs=False)

    def child_operation(self, argument, root):
        child = self.child_plan(argument, root)
        key = argument.operation_id
        record = self.service.ledger.get(key)
        if record.task_id != child.task_id or operation_id(child, record.step_id, record.revision) != key:
            raise PipelineError("operation_scope")
        return child, key

    @activity.defn(name="ui.edit.observe.v1")
    def edit_observe(self, argument: UIEditingActivityInput) -> OperationRecord:
        return self.invoke(
            argument, lambda root: self.service.observe_step(*self.child_operation(argument, root)), verify_inputs=False
        )

    @activity.defn(name="ui.edit.collect.v1")
    def edit_collect(self, argument: UIEditingActivityInput) -> OperationRecord:
        return self.invoke(
            argument, lambda root: self.service.collect_step(*self.child_operation(argument, root)), verify_inputs=False
        )

    @activity.defn(name="ui.edit.uncertain.v1")
    def edit_uncertain(self, argument: UIEditingActivityInput) -> bool:
        def audit(root):
            try:
                _, key = self.child_operation(argument, root)
            except PipelineError as exc:
                if exc.code == "unknown_operation":
                    return True
                raise
            self.service.ledger.record_interruption(key)
            return True

        return self.invoke(argument, audit, verify_inputs=False)

    @activity.defn(name="ui.edit.cancel.v1")
    def edit_cancel(self, argument: UIEditingActivityInput) -> bool:
        def cancel(root):
            # Close the root gate before inspecting any provider receipts.
            self.service.ledger.request_cancel(root.task_id)
            confirmed = self.execution.cancel(root)
            with self.service.ledger.transaction() as db:
                rows = db.execute(
                    "SELECT task_id FROM ui_child_bindings WHERE root_task_id=?", (root.task_id,)
                ).fetchall()
            for row in rows:
                confirmed = self.execution.cancel(self.service.ledger.plan(row["task_id"])) and confirmed
            return confirmed

        return self.invoke(argument, cancel, verify_inputs=False)

    def registered(self):
        return [
            self.edit_prepare,
            self.edit_approval,
            self.edit_submit,
            self.edit_observe,
            self.edit_collect,
            self.edit_uncertain,
            self.edit_cancel,
        ]
