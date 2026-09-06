"""One shared acceptance/budget/recovery matrix for all UI adapters."""

import pytest

from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.contracts import Capability, Submission
from letsaigc.pipelines.errors import OutcomeUnknown, PipelineError
from letsaigc.pipelines.migrations import migrate_ui_ledger
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ApprovalRequest, Cost
from letsaigc.schemas.ui import UIAnalysisRequest, UIObservation, UIStepBinding


class Backend:
    capability = Capability(id="ui.analyze")

    def __init__(self):
        self.calls = 0
        self.actual = Cost(cost_usd=0.4)
        self.lose_receipt = False

    def submit(self, operation_id, arguments):
        self.calls += 1
        if self.lose_receipt:
            raise TimeoutError("synthetic transport loss")
        return Submission(request_id="request-1")

    def inspect(self, submission):
        return UIObservation(state="succeeded", actual=self.actual)

    def collect(self, submission):
        return [("analysis", b'{"elements":[]}', "application/json")]


@pytest.fixture(params=[2, 4, 5])
def context(tmp_path, request):
    backend = Backend()
    service = PipelineService(tmp_path, backends={"ui.analyze": backend})
    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=request.param)
    ref = service.artifacts.put("ui-test", "intake", b"frozen-input", role="original", media_type="image/png")
    request = UIAnalysisRequest(
        input={"kind": "manual", "inputs": [ref]},
        budget={
            "max_total_cost_usd": 1,
            "max_iteration_cost_usd": 0.6,
            "max_total_gpu_minutes": 0,
            "max_iteration_gpu_minutes": 0,
            "max_revisions": 2,
        },
    )
    plan = service.ui_plan("ui-test", request)
    binding = UIStepBinding(task_id=plan.task_id, step_id="analyze", capability="ui.analyze", inputs=[ref])
    return service, plan, binding, backend


def authorize(service, plan):
    service.ledger.consume_approval(approve(service.ledger, plan.task_id, plan.fingerprint))


def finish(service, plan, operation):
    service.observe_step(plan, operation.operation_id)
    return service.collect_step(plan, operation.operation_id)


def test_no_approval_forged_receipt_or_changed_input_means_zero_calls(context):
    service, plan, binding, backend = context
    with pytest.raises(PipelineError):
        service.submit_step(plan, binding, Cost(cost_usd=0.2))
    fake = ApprovalRequest(
        request_id="forged", task_id=plan.task_id, plan_fingerprint=plan.fingerprint, decision="approve"
    )
    with pytest.raises(PipelineError):
        service.ledger.consume_approval(fake)
    with pytest.raises(PipelineError):
        approve(service.ledger, plan.task_id, "0" * 64)
    authorize(service, plan)
    service.artifacts.resolve(binding.inputs[0]).write_bytes(b"changed")
    with pytest.raises(PipelineError) as caught:
        service.submit_step(plan, binding, Cost(cost_usd=0.2))
    assert caught.value.code == "artifact_changed"
    assert backend.calls == 0


def test_duplicate_submission_binding_conflict_and_unknown_hold(context):
    service, plan, binding, backend = context
    authorize(service, plan)
    backend.lose_receipt = True
    for _ in range(2):
        with pytest.raises(OutcomeUnknown):
            service.submit_step(plan, binding, Cost(cost_usd=0.2))
    assert backend.calls == 1
    assert service.ledger.usage(plan.task_id)["unsettled"] == Cost(cost_usd=0.2)
    alternate = service.artifacts.put(plan.task_id, "other", b"other", role="canonical")
    changed = binding.model_copy(update={"inputs": [alternate]})
    with pytest.raises(PipelineError) as caught:
        service.submit_step(plan, changed, Cost(cost_usd=0.2))
    assert caught.value.code == "step_conflict"
    assert backend.calls == 1


def test_unknown_actual_never_settles_as_zero(context):
    service, plan, binding, backend = context
    authorize(service, plan)
    operation = service.submit_step(plan, binding, Cost(cost_usd=0.2))
    backend.actual = None
    with pytest.raises(OutcomeUnknown):
        finish(service, plan, operation)
    assert service.ledger.usage(plan.task_id)["unsettled"] == Cost(cost_usd=0.2)


def test_reservation_adjusted_continues_then_insufficient_stops_new_spending(context):
    service, plan, binding, backend = context
    authorize(service, plan)
    operation = service.submit_step(plan, binding, Cost(cost_usd=0.2))
    done = finish(service, plan, operation)
    assert done.state == "succeeded"
    assert done.result["budget_exceeded"] is True  # Legacy reservation variance remains compatible.
    assert done.result["usage_verdict"] == "reservation_adjusted"
    assert service.submit_step(plan, binding, Cost(cost_usd=0.2)) == done
    assert backend.calls == 1
    next_binding = binding.model_copy(update={"step_id": "analyze.second"})
    second = service.submit_step(plan, next_binding, Cost(cost_usd=0.4))
    finish(service, plan, second)
    with pytest.raises(PipelineError) as caught:
        service.submit_step(plan, binding.model_copy(update={"step_id": "analyze.third"}), Cost(cost_usd=0.3))
    assert caught.value.code == "budget_insufficient"
    assert backend.calls == 2
    assert service.ledger.usage(plan.task_id)["actual"] == Cost(cost_usd=0.8)


def test_actual_exceeding_approval_fails_and_permanently_closes_gate(context):
    service, plan, binding, backend = context
    authorize(service, plan)
    operation = service.submit_step(plan, binding, Cost(cost_usd=0.2))
    backend.actual = Cost(cost_usd=0.7)
    done = finish(service, plan, operation)
    assert done.state == "failed"
    assert done.result["usage_verdict"] == "budget_exceeded"
    authorize(service, plan)  # Replaying approval cannot reopen a failed budget gate.
    with pytest.raises(PipelineError) as caught:
        service.submit_step(plan, binding.model_copy(update={"step_id": "analyze.second"}), Cost())
    assert caught.value.code == "budget_exceeded"
    assert backend.calls == 1
    assert service.ledger.usage(plan.task_id)["actual"] == Cost(cost_usd=0.7)


def test_cancel_blocks_prepared_operation(context):
    service, plan, binding, backend = context
    authorize(service, plan)
    operation = service.ledger.reserve(plan, binding.step_id, 0, Cost(cost_usd=0.2), ui_binding=binding)
    service.ledger.request_cancel(plan.task_id)
    with pytest.raises(PipelineError) as caught:
        service.ledger.begin_submit(operation.operation_id)
    assert caught.value.code == "cancelled"
    assert backend.calls == 0


def test_unknown_workflow_and_legacy_submit_never_fall_through_to_comfy(context):
    service, plan, binding, backend = context
    authorize(service, plan)
    with pytest.raises(PipelineError):
        service.submit(plan, 0)
    with pytest.raises(PipelineError):
        service.submit_step(plan, binding.model_copy(update={"step_id": "arbitrary-tool"}), Cost())
    assert backend.calls == 0


def test_total_actual_violation_and_frozen_call_limit(context):
    service, plan, binding, backend = context
    authorize(service, plan)
    for suffix in ("first", "second", "third"):
        operation = service.submit_step(
            plan, binding.model_copy(update={"step_id": "analyze." + suffix}), Cost(cost_usd=0.1)
        )
        done = finish(service, plan, operation)
    assert done.result["usage_verdict"] == "budget_exceeded"
    assert service.ledger.usage(plan.task_id)["actual"] == Cost(cost_usd=1.2)


def test_call_limit_is_persistent_even_for_zero_cost_operations(context):
    service, plan, binding, backend = context
    authorize(service, plan)
    backend.actual = Cost()
    for index in range(4):
        operation = service.submit_step(plan, binding.model_copy(update={"step_id": f"analyze.{index}"}), Cost())
        finish(service, plan, operation)
    with pytest.raises(PipelineError) as caught:
        service.submit_step(plan, binding.model_copy(update={"step_id": "analyze.fifth"}), Cost())
    assert caught.value.code == "call_limit"
    assert backend.calls == 4
