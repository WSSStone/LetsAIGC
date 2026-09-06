"""T020 child selection and budget safety regressions.

These tests use only synthetic artifacts and the provider-free child helpers.
They are intentionally independent from the original T019 approval tests.
"""

import pytest
from test_ui_child_approvals import authorize as authorize_child
from test_ui_child_approvals import family as family_helper
from test_ui_child_approvals import reserve as reserve_child

from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.pipeline import ArtifactRef, Cost
from letsaigc.schemas.ui import UIStepBinding

BUDGET = {
    "max_total_cost_usd": 1,
    "max_iteration_cost_usd": 0.6,
    "max_total_gpu_minutes": 10,
    "max_iteration_gpu_minutes": 5,
    "max_revisions": 2,
}


@pytest.fixture
def family(tmp_path):
    return family_helper.__wrapped__(tmp_path)


def test_child_rejects_forged_frozen_inputs(family):
    service, _, child = family
    plan = child("forged-input")
    authorize_child(service, plan)
    forged = service.artifacts.put(plan.task_id, "evil", b"not-the-registered-request", role="request")
    selection_ref = ArtifactRef.model_validate(plan.parameters["selection_ref"])
    binding = UIStepBinding(
        task_id=plan.task_id,
        step_id="gpu",
        revision=0,
        capability="ui.segment",
        inputs=[forged],
        selection_ref=selection_ref,
        selection_revision=0,
        selection_hash=selection_ref.sha256,
    )
    with pytest.raises(PipelineError) as caught:
        service.ledger.reserve(plan, "gpu", 0, Cost(cost_usd=0.2, gpu_minutes=1), ui_binding=binding)
    assert caught.value.code in {"step_conflict", "artifact_scope", "invalid_step"}


def test_new_selection_supersedes_old_child_across_purposes(family):
    service, _, child = family
    old = child("old-segmentation", purpose="segmentation")
    authorize_child(service, old)
    new = child("new-inpaint", revision=1, purpose="inpaint")
    authorize_child(service, new)
    with pytest.raises(PipelineError) as caught:
        reserve_child(service, old)
    assert caught.value.code == "selection_superseded"


def test_stricter_child_iteration_overrun_closes_root_gate(family):
    service, _, child = family
    strict_budget = {**BUDGET, "max_iteration_cost_usd": 0.3}
    plan = child("strict-iteration", budget=strict_budget)
    authorize_child(service, plan)
    operation = reserve_child(service, plan, cost=0.2)
    result = service.ledger.finish(operation.operation_id, Cost(cost_usd=0.4, gpu_minutes=1), {})
    assert result.state == "failed"
    assert result.result["usage_verdict"] == "budget_exceeded"

    later = child("after-strict-iteration", revision=1)
    with pytest.raises(PipelineError) as caught:
        authorize_child(service, later)
    assert caught.value.code in {"cancelled", "budget_exceeded"}


def test_root_reserve_includes_child_actual_usage(family):
    service, root, child = family
    plan = child("root-aggregate")
    authorize_child(service, plan)
    operation = reserve_child(service, plan, cost=0.3)
    service.ledger.finish(operation.operation_id, Cost(cost_usd=0.5, gpu_minutes=1), {})

    binding = UIStepBinding(task_id=root.task_id, step_id="analyze", capability="ui.analyze", inputs=root.inputs)
    with pytest.raises(PipelineError) as caught:
        service.ledger.reserve(root, "analyze", 0, Cost(cost_usd=0.6), ui_binding=binding)
    assert caught.value.code == "total_budget"


def test_two_revisions_after_initial_share_one_root_revision_budget(family):
    service, _, child = family
    for index, purpose in enumerate(("segmentation", "inpaint", "segmentation")):
        plan = child(f"shared-revision-{index}", revision=index, purpose=purpose)
        authorize_child(service, plan)
        operation = reserve_child(service, plan, cost=0, revision=index)
        service.ledger.finish(operation.operation_id, Cost(gpu_minutes=1), {})

    extra = child("shared-revision-extra", revision=3, purpose="inpaint")
    authorize_child(service, extra)
    with pytest.raises(PipelineError) as caught:
        reserve_child(service, extra, cost=0, revision=1)
    assert caught.value.code == "revision_limit"


def test_failed_revision_still_consumes_shared_revision_slot(family):
    service, _, child = family
    first = child("failed-revision-0", revision=0)
    authorize_child(service, first)
    operation = reserve_child(service, first, cost=0)
    service.ledger.finish(operation.operation_id, Cost(gpu_minutes=1), {"synthetic_failure": True}, failed=True)

    second = child("failed-revision-1", revision=1)
    authorize_child(service, second)
    operation = reserve_child(service, second, cost=0, revision=1)
    service.ledger.finish(operation.operation_id, Cost(gpu_minutes=1), {})

    third = child("failed-revision-2", revision=2)
    authorize_child(service, third)
    operation = reserve_child(service, third, cost=0, revision=2)
    service.ledger.finish(operation.operation_id, Cost(gpu_minutes=1), {})

    extra = child("failed-revision-extra", revision=3)
    authorize_child(service, extra)
    with pytest.raises(PipelineError) as caught:
        reserve_child(service, extra, cost=0, revision=1)
    assert caught.value.code == "revision_limit"
