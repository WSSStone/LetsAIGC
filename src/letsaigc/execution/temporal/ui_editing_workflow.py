"""Sequential editing orchestration with separate, exact child approvals."""

import asyncio

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from ...schemas.pipeline import ApprovalRequest, ArtifactRef, OperationRecord, PipelineState, StepRun, operation_id
    from ...ui_analysis.editing import EditingPreparation
    from .policies import options
    from .smoke_workflow import failure_code
    from .ui_messages import UIEditingActivityInput, UIEditingWorkflowInput
    from .ui_workflow import UIAnalysisWorkflow


@workflow.defn(name="letsaigc.ui.editing.v1")
class UIEditingWorkflow(UIAnalysisWorkflow):
    @workflow.query(name="revision")
    def revision(self) -> ArtifactRef | None:
        return self.input.revision_ref if hasattr(self, "input") else None

    @workflow.run
    async def run(self, argument: UIEditingWorkflowInput):
        self.child = argument.child
        self.child_approved = argument.child_approved
        self.child_pending = argument.initial_child_approval
        return await super().run(argument)

    @workflow.update(name="approval")
    def approval(self, request: ApprovalRequest) -> str:
        if self.phase_index < 7:
            return super().approval(request)
        if self.current is None or self.child is None:
            raise ApplicationError("not_ready", non_retryable=True)
        if (request.task_id, request.plan_fingerprint) != (self.child.task_id, self.child.fingerprint):
            raise ApplicationError("approval_mismatch", non_retryable=True)
        if self.accepted_request == request.request_id or self.child_pending == request:
            return "already_registered"
        if self.current.state != PipelineState.awaiting_approval or self.child_pending is not None:
            raise ApplicationError("approval_closed", non_retryable=True)
        self.child_pending = request
        return "registered_pending_validation"

    async def call(self, name, *, result_type=bool, **values):
        if name == "cancel":
            name = "edit.cancel"
        if not name.startswith("edit."):
            return await super().call(name, result_type=result_type, **values)
        timed = name in {"edit.submit", "edit.observe", "edit.collect"}
        timeout = min(self.input.activity_timeout_seconds, 600 if name == "edit.submit" else 180)
        if timed:
            now = workflow.now().timestamp()
            self.active_seconds += max(0, now - self.active_tick) if self.active_tick is not None else 0
            self.active_tick = now
            remaining = self.active_limit - self.active_seconds
            if remaining <= 0:
                self.resource_expired = True
                raise asyncio.CancelledError
            timeout = min(timeout, max(1, int(remaining)))
        try:
            return await workflow.execute_activity(
                f"ui.{name}.v1",
                UIEditingActivityInput(
                    task_id=self.input.plan.task_id,
                    plan_fingerprint=self.input.plan.fingerprint,
                    child=self.child,
                    revision_ref=self.input.revision_ref,
                    **values,
                ),
                result_type=result_type,
                **options(submit=name in {"edit.submit", "edit.cancel"}, timeout=timeout),
            )
        except (ActivityError, CancelledError) as exc:
            if is_cancelled_exception(exc):
                raise asyncio.CancelledError from None
            raise
        finally:
            if timed:
                now = workflow.now().timestamp()
                self.active_seconds += max(0, now - self.active_tick)
                self.active_tick = now

    async def finish_analysis(self):
        observations = 0
        self.active_tick = None
        while True:
            if self.cancelled or self.active_seconds >= self.active_limit:
                if self.active_seconds >= self.active_limit:
                    self.resource_expired = True
                self.cancelled = self.cancelling = True
                await self.publish(PipelineState.cancel_requested)
                try:
                    confirmed = await self.call("cancel")
                except ActivityError:
                    confirmed = False
                if confirmed:
                    await self.publish(
                        PipelineState.failed if self.resource_expired else PipelineState.cancelled,
                        "active_time_limit" if self.resource_expired else "cancellation_confirmed",
                    )
                    return self.current
                await self.await_reconciliation("cancellation_outcome_unknown")
                continue
            if self.child is None or not self.child_approved:
                try:
                    prepared = await self.call("edit.prepare", result_type=EditingPreparation)
                except ActivityError as exc:
                    code = failure_code(exc)
                    if code in {"dependency_not_ready", "model_not_ready", "resource_insufficient",
                                "resource_release_unknown", "dependency_changed", "capability_not_ready"}:
                        await self.await_reconciliation(code)
                        continue
                    raise
                refs = {ref.artifact_id: ref for ref in [*self.current.artifacts, *prepared.artifacts]}
                self.current = self.current.model_copy(update={"artifacts": list(refs.values())})
                if prepared.state in {"succeeded", "failed"}:
                    await self.publish(PipelineState(prepared.state), prepared.reason)
                    return self.current
                if prepared.state == "awaiting_reconciliation":
                    await self.await_reconciliation(prepared.reason or "outcome_unknown")
                    continue
                if prepared.state == "awaiting_selection":
                    await self.publish(PipelineState.running, "awaiting_selection")
                    await workflow.sleep(self.input.poll_seconds)
                    observations += 1
                    await self.check_continue(observations)
                    continue
                if self.child != prepared.child:
                    if self.child is not None:
                        self.child_pending = None
                    self.child = prepared.child
                if self.child is None:
                    raise ApplicationError("unknown_child", non_retryable=True)
                self.active_tick = None
                await self.publish(PipelineState.awaiting_approval)
                try:
                    await workflow.wait_condition(
                        lambda: self.child_pending is not None or self.cancelled, timeout=self.input.poll_seconds
                    )
                except TimeoutError:
                    observations += 1
                    await self.check_continue(observations)
                    continue
                if self.cancelled:
                    continue
                receipt = self.child_pending
                try:
                    self.child_approved = await self.call("edit.approval", approval=receipt)
                except ActivityError as exc:
                    self.child_pending = None
                    await self.publish(PipelineState.awaiting_approval, failure_code(exc))
                    continue
                self.child_pending = None
                self.accepted_request = receipt.request_id
                if not self.child_approved:
                    await self.publish(PipelineState.rejected, "user_rejected")
                    return self.current
            step_id = self.child.envelope.allowed_capabilities[0].removeprefix("ui.")
            key = operation_id(self.child, step_id, 0)
            await self.publish(PipelineState.running)
            submitted = False
            try:
                operation = await self.call("edit.submit", result_type=OperationRecord)
                submitted = operation.state not in {"prepared", "succeeded", "failed"}
                while operation.state not in {"succeeded", "failed"} and not self.cancelled:
                    operation = await self.call("edit.observe", operation_id=key, result_type=OperationRecord)
                    if operation.result.get("observation", {}).get("state") in {"succeeded", "failed"}:
                        operation = await self.call("edit.collect", operation_id=key, result_type=OperationRecord)
                        break
                    observations += 1
                    await self.check_continue(observations)
                    await workflow.sleep(self.input.poll_seconds)
                if self.cancelled:
                    continue
            except ActivityError as exc:
                code = failure_code(exc)
                if code in {"selection_superseded", "approval_required"}:
                    self.child, self.child_approved = None, False
                    self.active_tick = None
                    continue
                if code == "cancelled":
                    self.cancelled = True
                    continue
                if submitted or code in {
                    "outcome_unknown",
                    "resource_busy",
                    "resource_release_unknown",
                    "activity_interrupted",
                    "dependency_failure",
                    "awaiting_reconciliation",
                }:
                    await self.call("edit.uncertain", operation_id=key)
                    await self.await_reconciliation(code)
                    continue
                await self.publish(PipelineState.failed, code)
                return self.current
            refs = {ref.artifact_id: ref for ref in self.current.artifacts}
            for value in operation.result.get("artifacts", []):
                ref = ArtifactRef.model_validate(value)
                refs[ref.artifact_id] = ref
            step = StepRun(step_id=step_id, operation_id=key, revision=0, state=operation.state)
            self.current = self.current.model_copy(
                update={"artifacts": list(refs.values()), "steps": [*self.current.steps, step]}
            )
            if operation.state == "failed":
                await self.publish(
                    PipelineState.failed,
                    "budget_exceeded"
                    if operation.result.get("usage_verdict") == "budget_exceeded"
                    else "provider_failed",
                )
                return self.current
            self.child, self.child_approved = None, False
            self.active_tick = None
            observations += 1
            await self.check_continue(observations)

    async def check_continue(self, observations):
        if observations >= self.input.observations_per_run or workflow.info().is_continue_as_new_suggested():
            await workflow.wait_condition(workflow.all_handlers_finished)
            workflow.continue_as_new(
                self.input.model_copy(
                    update={
                        "run": self.current,
                        "approved": self.approved,
                        "cancelled": self.cancelled,
                        "initial_approval": None,
                        "phase_index": self.phase_index,
                        "position": self.position,
                        "active_seconds": self.active_seconds,
                        "active_limit_seconds": self.active_limit,
                        "resource_expired": self.resource_expired,
                        "child": self.child,
                        "child_approved": self.child_approved,
                        "initial_child_approval": self.child_pending,
                    }
                )
            )
