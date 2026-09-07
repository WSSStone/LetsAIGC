"""Sequential manual/search analysis; external work lives exclusively in Activities."""

import asyncio

from temporalio import workflow
from temporalio.exceptions import ActivityError, CancelledError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from ...schemas.pipeline import ArtifactRef, OperationRecord, PipelineRun, PipelineState, StepRun, operation_id
    from ...ui_analysis.execution import PHASES, UIStepPreparation
    from .policies import options
    from .smoke_workflow import PipelineWorkflow, failure_code
    from .ui_messages import UIActivityInput, UIWorkflowInput


@workflow.defn(name="letsaigc.ui.analysis.v1")
class UIAnalysisWorkflow(PipelineWorkflow):
    @workflow.run
    async def run(self, argument: UIWorkflowInput) -> PipelineRun:
        self.phase_index, self.position = argument.phase_index, argument.position
        self.active_seconds = argument.active_seconds
        self.active_limit = argument.active_limit_seconds
        self.resource_expired = argument.resource_expired
        self.active_tick = None
        self.pending = argument.initial_approval
        return await self.run_pipeline(argument)

    async def call(self, name, *, result_type=bool, **values):
        if name in {"validate", "approval"}:
            return await super().call(name, result_type=result_type, **values)
        argument = UIActivityInput(
            task_id=self.input.plan.task_id,
            plan_fingerprint=self.input.plan.fingerprint,
            phase=PHASES[min(self.phase_index, 6)],
            position=self.position,
            **values,
        )
        timeout = min(self.input.activity_timeout_seconds, 600 if name in {"prepare", "submit"} else 180)
        timed = name in {"prepare", "submit", "observe", "collect"}
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
                argument,
                result_type=result_type,
                **options(submit=name in {"submit", "cancel"}, timeout=timeout),
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

    async def await_reconciliation(self, code):
        try:
            await super().await_reconciliation(code)
        finally:
            self.active_tick = None

    async def advance(self):
        if not self.cancelled and not self.approved:
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
        observations = 0
        while True:
            if self.active_seconds >= self.active_limit:
                self.cancelled = self.resource_expired = True
            if self.cancelled:
                self.cancelling = True
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
            if self.phase_index == len(PHASES):
                return await self.finish_analysis()
            await self.publish(PipelineState.running)
            prepared = await self.call("prepare", result_type=UIStepPreparation)
            self.active_limit = min(self.active_limit, prepared.active_limit_seconds)
            binding = prepared.binding
            key = operation_id(self.input.plan, binding.step_id, 0)
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
                    await self.check_continue(observations)
                    await workflow.sleep(self.input.poll_seconds)
                if self.cancelled:
                    continue
            except ActivityError as exc:
                code = failure_code(exc)
                if code == "cancelled":
                    self.cancelled = True
                    continue
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
                await self.publish(PipelineState.failed, code)
                return self.current
            # Individual OCR payloads stay in the ledger; merged text and the
            # small public output set keep queries/Continue-As-New bounded.
            artifacts = (
                []
                if binding.capability == "ui.ocr"
                else [ArtifactRef.model_validate(item) for item in operation.result.get("artifacts", [])]
            )
            step = StepRun(step_id=binding.step_id, operation_id=key, revision=0, state=operation.state)
            refs = {ref.artifact_id: ref for ref in [*self.current.artifacts, *artifacts, *prepared.context_refs]}
            self.current = self.current.model_copy(
                update={"steps": [*self.current.steps, step], "artifacts": list(refs.values())}
            )
            if operation.state == "failed":
                reason = (
                    "budget_exceeded"
                    if operation.result.get("usage_verdict") == "budget_exceeded"
                    else "provider_failed"
                )
                await self.publish(PipelineState.failed, reason)
                return self.current
            if self.position + 1 < prepared.repeat_count:
                self.position += 1
            else:
                self.phase_index += 1
                self.position = 0
            observations += 1
            await self.check_continue(observations)

    async def finish_analysis(self):
        await self.publish(PipelineState.succeeded)
        return self.current

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
                    }
                )
            )
