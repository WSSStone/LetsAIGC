"""Editing activities retain root ownership while executing exact child plans."""

from temporalio import activity

from ...pipelines.errors import PipelineError
from ...schemas.pipeline import Cost, OperationRecord, PipelinePlan, operation_id
from ...ui_analysis.editing import EditingExecution, EditingPreparation, _release_confirmed
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

    @activity.defn(name="ui.edit.approval-target.v1")
    def edit_approval_target(self, argument: UIEditingActivityInput) -> PipelinePlan:
        def consume(root):
            receipt = argument.approval
            if receipt is None:
                raise PipelineError("approval_required")
            child = self.service.checked_plan(receipt.task_id, receipt.plan_fingerprint)
            if self.editing.child_root(child) != root.task_id:
                raise PipelineError("operation_scope")
            self.editing.validate_current(child)
            if child.workflow_type in {"ui_cloud_guide", "ui_cloud_inpaint"}:
                from ...ui_analysis.cloud_editing import validate_cloud_target

                validate_cloud_target(self.service, child)
            if argument.revision_ref is not None:
                from ...ui_analysis.revision_execution import RevisionExecution

                prepared = RevisionExecution(self.service).prepare(root, argument.revision_ref)
                if prepared.child != child:
                    raise PipelineError("operation_scope")
            if receipt.decision == "approve" and not self.service.ledger.list_operations(child.task_id):
                usage = self.service.ledger.usage(root.task_id, include_children=True)
                limits = self.service.ledger.effective_budget(root.task_id)
                if child.parameters.get("cloud_retry"):
                    from ...schemas.ui_cloud import CloudImageRetry

                    limits = CloudImageRetry.model_validate(child.parameters["cloud_retry"]).budget_after
                budget = child.envelope.budget
                held_cost = sum(value.cost_usd for value in usage.values())
                held_gpu = sum(value.gpu_minutes for value in usage.values())
                if (held_cost + budget.max_iteration_cost_usd > limits.max_total_cost_usd + 1e-9
                        or held_gpu + budget.max_iteration_gpu_minutes > limits.max_total_gpu_minutes + 1e-9):
                    raise PipelineError("budget_exceeded")
            if child.workflow_type == "ui_inpaint":
                parent = self.service.ledger.plan(child.parameters["parent_task_id"])
                if parent.workflow_type != "ui_segmentation" or self.editing.child_root(parent) != root.task_id:
                    raise PipelineError("operation_scope")
                self.service.checked_plan(parent.task_id, parent.fingerprint)
                self.editing.validate_current(parent)
                if not any(op.state == "succeeded" and _release_confirmed(op)
                           for op in self.service.ledger.list_operations(parent.task_id)):
                    raise PipelineError("resource_release_unknown")
            # Existing transaction checks receipt identity, the root gate and
            # competing in-flight children before replacing pending candidates.
            self.service.ledger.consume_approval(receipt)
            return child

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
                budget_root = self.service.ledger._root_task_id(db, root.task_id)
                rows = db.execute(
                    "SELECT task_id FROM ui_child_bindings WHERE root_task_id=?", (budget_root,)
                ).fetchall()
            for row in rows:
                confirmed = self.execution.cancel(self.service.ledger.plan(row["task_id"])) and confirmed
            return confirmed

        return self.invoke(argument, cancel, verify_inputs=False)

    def registered(self):
        return [
            self.edit_prepare,
            self.edit_approval,
            self.edit_approval_target,
            self.edit_submit,
            self.edit_observe,
            self.edit_collect,
            self.edit_uncertain,
            self.edit_cancel,
        ]
