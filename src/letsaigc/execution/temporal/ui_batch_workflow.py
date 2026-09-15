"""One live single-image child per batch, with root-owned cancellation."""

import asyncio

from temporalio import workflow
from temporalio.exceptions import ActivityError, CancelledError, ChildWorkflowError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from ...schemas.pipeline import PipelineRun, PipelineState
    from ...ui_analysis.batch import BatchPreparation
    from .policies import options
    from .smoke_workflow import PipelineWorkflow, failure_code
    from .ui_batch_messages import UIBatchActivityInput, UIBatchWorkflowInput
    from .ui_messages import UIEditingWorkflowInput, UIWorkflowInput


@workflow.defn(name="letsaigc.ui.batch.v1")
class UIBatchWorkflow(PipelineWorkflow):
    @workflow.run
    async def run(self, argument: UIBatchWorkflowInput) -> PipelineRun:
        self.pending = argument.initial_approval
        self.active_child = None
        self.child_cancel_requested = False
        return await self.run_pipeline(argument)

    async def call(self, name, *, result_type=bool, **values):
        if name in {"validate", "approval"}:
            return await super().call(name, result_type=result_type, **values)
        try:
            return await workflow.execute_activity(
                f"ui.batch.{name}.v1",
                UIBatchActivityInput(
                    task_id=self.input.plan.task_id, plan_fingerprint=self.input.plan.fingerprint, **values,
                ),
                result_type=result_type,
                **options(submit=name in {"prepare", "cancel"}, timeout=self.input.activity_timeout_seconds),
            )
        except (ActivityError, CancelledError) as exc:
            if is_cancelled_exception(exc):
                raise asyncio.CancelledError from None
            raise

    async def _cancel_children(self):
        self.cancelling = True
        await self.publish(PipelineState.cancel_requested)
        # Close the durable gate before Temporal delivers any child cancellation.
        try:
            confirmed = await self.call("cancel")
        except ActivityError:
            # Gate closure is not established, so do not send child cancellation.
            await self.await_reconciliation("cancellation_outcome_unknown")
            return None
        if self.active_child is not None:
            if not self.child_cancel_requested:
                self.active_child.cancel()
                self.child_cancel_requested = True
        if not confirmed:
            # The child can itself be waiting for reconciliation. Keep its handle
            # and ledger ownership without blocking the parent's reconcile update.
            await self.await_reconciliation("cancellation_outcome_unknown")
            return None
        if self.active_child is not None:
            try:
                if not self.active_child.done():
                    await self.active_child.signal("reconcile_cancelled")
                await workflow.wait_condition(
                    self.active_child.done, timeout=min(60, self.input.activity_timeout_seconds),
                )
                await self.active_child
            except (ChildWorkflowError, asyncio.CancelledError):
                if not self.active_child.done():
                    # Another native parent cancellation can interrupt the signal
                    # or wait. It is not a child completion acknowledgment.
                    raise
            except TimeoutError:
                await self.await_reconciliation("child_cancellation_pending")
                return None
            self.active_child = None
        await self.publish(PipelineState.cancelled, "cancellation_confirmed")
        return self.current

    async def advance(self):
        if not self.approved and not self.cancelled:
            await self.call("validate")
            await self.publish(PipelineState.awaiting_approval)
            while not self.approved and not self.cancelled:
                await workflow.wait_condition(lambda: self.pending is not None or self.cancelled)
                if self.cancelled:
                    break
                request = self.pending
                try:
                    self.approved = await self.call("approval", approval=request)
                except ActivityError as exc:
                    self.pending = None
                    await self.publish(PipelineState.awaiting_approval, failure_code(exc))
                    continue
                self.pending = None
                self.accepted_request = request.request_id
                if not self.approved:
                    await self.publish(PipelineState.rejected, "user_rejected")
                    return self.current
        completed = 0
        while True:
            if self.cancelled:
                result = await self._cancel_children()
                if result is not None:
                    return result
                continue
            try:
                prepared = await self.call("prepare", result_type=BatchPreparation)
            except ActivityError as exc:
                code = failure_code(exc)
                if code == "cancelled":
                    self.cancelled = True
                    continue
                if code not in {
                    "outcome_unknown", "awaiting_reconciliation", "resource_release_unknown",
                    "resource_busy", "activity_interrupted", "dependency_failure", "ledger_unavailable",
                    "dependency_not_ready", "dependency_changed", "model_not_ready",
                    "resource_insufficient", "capability_not_ready", "approval_required", "awaiting_approval",
                }:
                    raise
                # Supply may already have an accepted provider receipt. Preserve
                # its operation and budget until an explicit reconciliation.
                await self.await_reconciliation(code)
                continue
            if self.cancelled:
                continue
            if prepared.child is None:
                if prepared.state in {"succeeded", "partial", "failed", "cancelled"}:
                    return await self.finish_batch(prepared)
                await self.await_reconciliation(prepared.reason or "batch_not_ready")
                continue
            child = prepared.child
            await self.publish(PipelineState.running)
            if self.cancelled:
                continue
            argument_class = UIEditingWorkflowInput if prepared.editing else UIWorkflowInput
            self.child_cancel_requested = False
            self.active_child = await workflow.start_child_workflow(
                "letsaigc.ui.editing.v1" if prepared.editing else "letsaigc.ui.analysis.v1",
                argument_class(plan=child, approved=True, poll_seconds=self.input.poll_seconds,
                                observations_per_run=self.input.observations_per_run,
                                activity_timeout_seconds=self.input.activity_timeout_seconds,
                                active_limit_seconds=prepared.active_limit_seconds),
                id=child.task_id,
                result_type=PipelineRun,
                parent_close_policy=workflow.ParentClosePolicy.REQUEST_CANCEL,
                cancellation_type=workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
            cancellation = asyncio.create_task(workflow.wait_condition(lambda: self.cancelled))
            try:
                await asyncio.wait([self.active_child, cancellation], return_when=asyncio.FIRST_COMPLETED)
                if self.cancelled:
                    continue
                while not self.cancelled:
                    try:
                        result = await self.active_child
                        await self.call("complete", child_run=result, result_type=BatchPreparation)
                        break
                    except ChildWorkflowError:
                        # Keep the same handle: workflow failure does not prove
                        # that accepted provider work has stopped.
                        await self.await_reconciliation("child_workflow_failed")
                    except ActivityError as exc:
                        code = failure_code(exc)
                        if code not in {
                            "outcome_unknown", "awaiting_reconciliation", "resource_release_unknown",
                            "activity_interrupted", "dependency_failure", "ledger_unavailable",
                            "approval_required", "awaiting_approval",
                        }:
                            raise
                        # A gate is never an independent image failure. Retry
                        # settlement only after reconcile; never start another child.
                        await self.await_reconciliation(code)
                if self.cancelled:
                    continue
            finally:
                cancellation.cancel()
            self.active_child = None
            completed += 1
            snapshot = await self.call("checkpoint", result_type=BatchPreparation)
            if self.cancelled:
                continue
            if snapshot.state in {"succeeded", "partial"}:
                return await self.finish_batch(snapshot)
            if completed >= self.input.observations_per_run or workflow.info().is_continue_as_new_suggested():
                await workflow.wait_condition(workflow.all_handlers_finished)
                if self.cancelled:
                    continue
                workflow.continue_as_new(self.input.model_copy(update={
                    "run": self.current, "approved": self.approved,
                    "cancelled": self.cancelled, "initial_approval": None,
                }))

    async def finish_batch(self, prepared):
        if self.cancelled:
            raise asyncio.CancelledError
        self.current = self.current.model_copy(update={"artifacts": prepared.artifacts})
        state = PipelineState.succeeded if prepared.state == "succeeded" else PipelineState.failed
        if prepared.state == "cancelled":
            state = PipelineState.cancelled
        await self.publish(state, "partial_results" if prepared.state == "partial" else prepared.reason)
        if self.cancelled and state != PipelineState.cancelled:
            raise asyncio.CancelledError
        return self.current
