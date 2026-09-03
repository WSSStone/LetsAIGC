"""Deterministic v1 orchestration, shared by explicit registered workflow types."""

from __future__ import annotations

import asyncio

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from ...schemas.pipeline import (
        ApprovalRequest,
        ArtifactRef,
        OperationRecord,
        PipelineRun,
        PipelineState,
        StepRun,
        operation_id,
    )
    from .messages import ActivityInput, WorkflowInput
    from .policies import options


def failure_code(exc: ActivityError) -> str:
    cause = exc.cause
    return cause.type if isinstance(cause, ApplicationError) and cause.type else "activity_interrupted"


class PipelineWorkflow:
    def __init__(self):
        self.current: PipelineRun | None = None
        self.pending: ApprovalRequest | None = None
        self.accepted_request: str | None = None
        self.cancelled = False
        self.reconciliation = False

    @workflow.query(name="state")
    def state(self) -> PipelineRun | None:
        return self.current

    @workflow.update(name="approval")
    def approval(self, request: ApprovalRequest) -> str:
        if self.current is None:
            raise ApplicationError("not_ready", non_retryable=True)
        if request.task_id != self.current.task_id or request.plan_fingerprint != self.current.plan_fingerprint:
            raise ApplicationError("approval_mismatch", non_retryable=True)
        if self.accepted_request == request.request_id or self.pending == request:
            return "already_registered"
        if self.current.state != PipelineState.awaiting_approval or self.pending is not None:
            raise ApplicationError("approval_closed", non_retryable=True)
        self.pending = request
        return "registered_pending_validation"

    @workflow.update(name="cancel")
    def cancel(self) -> str:
        self.cancelled = True
        self.reconciliation = True
        return "cancel_requested"

    @workflow.update(name="reconcile")
    def reconcile(self) -> str:
        if self.current is None or self.current.state != PipelineState.awaiting_reconciliation:
            raise ApplicationError("reconciliation_not_required", non_retryable=True)
        self.reconciliation = True
        return "reconciliation_requested"

    async def call(self, name: str, *, result_type=bool, **values):
        argument = ActivityInput(
            task_id=self.input.plan.task_id,
            plan_fingerprint=self.input.plan.fingerprint,
            revision=self.current.revision,
            **values,
        )
        timeout = min(
            self.input.activity_timeout_seconds,
            {
                "approval": 30,
                "observe": 30,
                "uncertain": 30,
                "cancel": 60,
                "collect": 180,
                "project": 60,
                "validate": 600,
                "submit": 600,
            }[name],
        )
        try:
            return await workflow.execute_activity(
                f"pipeline.{name}.v1",
                argument,
                result_type=result_type,
                **options(submit=name in {"submit", "cancel"}, timeout=timeout),
            )
        except (ActivityError, CancelledError) as exc:
            if is_cancelled_exception(exc):
                # SDK Activity cancellation wraps its cause in ActivityError.
                # Never mistake it for a projection failure and retry forever.
                raise asyncio.CancelledError from None
            raise

    async def publish(self, state: PipelineState, reason: str | None = None):
        self.current = self.current.model_copy(
            update={
                "state": state,
                "stop_reason": reason,
                "projection_sequence": self.current.projection_sequence + 1,
            }
        )
        # A broken projection pauses here; no business action is repeated to repair it.
        while True:
            try:
                self.current = self.current.model_copy(update={"stop_reason": reason})
                await self.call("project", run=self.current)
                return
            except ActivityError:
                self.current = self.current.model_copy(update={"stop_reason": "projection_unavailable"})
                if self.cancelled:
                    if state in {PipelineState.cancel_requested, PipelineState.cancelled}:
                        return
                    raise asyncio.CancelledError from None
                await workflow.sleep(self.input.poll_seconds)

    async def await_reconciliation(self, code: str):
        self.reconciliation = False
        await self.publish(PipelineState.awaiting_reconciliation, code)
        await workflow.wait_condition(lambda: self.reconciliation or (self.cancelled and not self.cancelling))

    async def run_pipeline(self, argument: WorkflowInput) -> PipelineRun:
        self.input = argument
        self.cancelled = argument.cancelled
        self.cancelling = False
        info = workflow.info()
        self.current = argument.run or PipelineRun(
            task_id=argument.plan.task_id,
            workflow_id=info.workflow_id,
            temporal_run_id=info.run_id,
            plan_fingerprint=argument.plan.fingerprint,
        )
        self.current = self.current.model_copy(update={"temporal_run_id": info.run_id})
        self.approved = argument.approved
        while True:
            try:
                return await self.advance()
            except asyncio.CancelledError:
                # Native cancellation also uses confirmed backend cleanup; termination
                # cannot run cleanup and must be handled by an operator from the ledger.
                self.cancelled = True
            except ActivityError as exc:
                await self.publish(PipelineState.failed, failure_code(exc))
                return self.current

    async def advance(self) -> PipelineRun:
        if not self.cancelled and not self.approved:
            await self.call("validate")
        if not self.approved and not self.cancelled:
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

        observations = 0
        while True:
            if self.cancelled:
                self.cancelling = True
                await self.publish(PipelineState.cancel_requested)
                try:
                    confirmed = await self.call("cancel")
                except ActivityError:
                    confirmed = False
                if confirmed:
                    await self.publish(PipelineState.cancelled, "cancellation_confirmed")
                    return self.current
                await self.await_reconciliation("cancellation_outcome_unknown")
                continue
            await self.publish(PipelineState.running)
            key = operation_id(self.input.plan, "generate", self.current.revision)
            submitted = False
            try:
                operation = await self.call("submit", result_type=OperationRecord)
                submitted = operation.state not in {"prepared", "succeeded", "failed"}
                while operation.state not in {"succeeded", "failed"} and not self.cancelled:
                    operation = await self.call("observe", operation_id=key, result_type=OperationRecord)
                    if operation.result.get("observation", {}).get("state") in {"succeeded", "failed"}:
                        operation = await self.call("collect", operation_id=key, result_type=OperationRecord)
                        break
                    observations += 1
                    if (
                        observations >= self.input.observations_per_run
                        or workflow.info().is_continue_as_new_suggested()
                    ):
                        await workflow.wait_condition(workflow.all_handlers_finished)
                        workflow.continue_as_new(
                            self.input.model_copy(
                                update={
                                    "run": self.current,
                                    "approved": self.approved,
                                    "cancelled": self.cancelled,
                                }
                            )
                        )
                    await workflow.sleep(self.input.poll_seconds)
                if self.cancelled:
                    continue
            except ActivityError as exc:
                code = failure_code(exc)
                if submitted or code in {
                    "outcome_unknown",
                    "resource_busy",
                    "activity_interrupted",
                    "dependency_failure",
                }:
                    try:
                        await self.call("uncertain", operation_id=key)
                    except ActivityError:
                        code = "ledger_unavailable"
                    await self.await_reconciliation(code)
                    continue
                if code == "cancelled":
                    self.cancelled = True
                    continue
                await self.publish(PipelineState.failed, code)
                return self.current
            artifacts = [ArtifactRef.model_validate(item) for item in operation.result.get("artifacts", [])]
            step = StepRun(
                step_id="generate",
                operation_id=key,
                revision=self.current.revision,
                state=operation.state,
                artifacts=artifacts,
            )
            self.current = self.current.model_copy(
                update={
                    "steps": [*self.current.steps, step],
                    "artifacts": [*self.current.artifacts, *artifacts],
                }
            )
            if operation.result.get("budget_exceeded") or operation.state == "failed":
                await self.publish(
                    PipelineState.failed,
                    "budget_exceeded" if operation.result.get("budget_exceeded") else "provider_failed",
                )
                return self.current
            accept_after = self.input.plan.parameters.get("accept_after_revision", 0)
            if self.current.revision >= accept_after:
                await self.publish(PipelineState.succeeded)
                return self.current
            if self.current.revision >= self.input.plan.envelope.budget.max_revisions:
                await self.publish(PipelineState.failed, "revision_limit")
                return self.current
            self.current = self.current.model_copy(update={"revision": self.current.revision + 1})


@workflow.defn(name="letsaigc.smoke.v1")
class SmokeWorkflow(PipelineWorkflow):
    @workflow.run
    async def run(self, argument: WorkflowInput) -> PipelineRun:
        return await self.run_pipeline(argument)


@workflow.defn(name="letsaigc.comfy.v1")
class ComfyGenerationWorkflow(PipelineWorkflow):
    @workflow.run
    async def run(self, argument: WorkflowInput) -> PipelineRun:
        return await self.run_pipeline(argument)
