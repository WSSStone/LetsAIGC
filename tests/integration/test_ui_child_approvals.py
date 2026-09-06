"""T019 GPU/parent deltas only; general denial/retry matrix lives in T003.

The proposed service entrypoint is documented in contracts/editing.md. Missing
T020 support is an explicit xfail, never a successful GPU acceptance result.
Once the entrypoint exists, all assertions run without an exception waiver.
"""

import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef, Cost, canonical_json
from letsaigc.schemas.ui import UIAnalysisRequest, UIResourceLimits, UIStepBinding

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/contracts/ui-edit-contracts.json"
BUDGET = {
    "max_total_cost_usd": 1, "max_iteration_cost_usd": 0.6,
    "max_total_gpu_minutes": 10, "max_iteration_gpu_minutes": 5, "max_revisions": 2,
}


def authorize(service, plan):
    service.ledger.consume_approval(approve(service.ledger, plan.task_id, plan.fingerprint))


@pytest.fixture
def family(tmp_path):
    if not hasattr(PipelineService, "ui_child_plan"):
        pytest.xfail("T020: ui_child_plan/root budget groups are not implemented")
    service = PipelineService(tmp_path, ui_schema=5)
    stream = BytesIO()
    Image.new("RGB", (64, 48), "white").save(stream, format="PNG")
    image = service.artifacts.put("root", "input", stream.getvalue(), role="original", media_type="image/png")
    root = service.ui_plan("root", UIAnalysisRequest(
        input={"kind": "manual", "inputs": [image]}, output_mode="reconstruct",
        reconstruction_target="scene_background", selection_mode="deferred", budget=BUDGET,
    ))
    authorize(service, root)

    def child(task_id, *, revision=0, source_ids=None, budget=None, parent_task_id="root", purpose="segmentation"):
        value = json.loads(FIXTURE.read_text())["selection"]
        value["sources"][0]["source_id"] = image.artifact_id
        value["sources"][0]["original_sha256"] = image.sha256
        value["sources"][0]["target_regions"][0]["xyxy"][0] = revision
        selection_ref = service.artifacts.put(
            "root", f"selection-{revision}", canonical_json(value).encode(), role="selection",
        )
        # Child content is registered in its own scope before planning; no raw
        # path, arbitrary provider arguments or network backend is involved.
        canonical = service.artifacts.put(task_id, "input", stream.getvalue(), role="canonical", media_type="image/png")
        local_selection = service.artifacts.put(
            task_id, "input", canonical_json(value).encode(), role="selection",
        )
        snapshot = service.artifacts.put(task_id, "model", b'{"fixture":true,"model":"sam2"}', role="model_snapshot")
        request_content = {
            "schema_version": 1, "canonical_ref": canonical.model_dump(mode="json"),
            "selection_ref": local_selection.model_dump(mode="json"), "selection_revision": revision,
            "selection_hash": local_selection.sha256,
            "prompts": [{"element_id": "region-1", "box": [revision, 0, 64, 48], "points": []}],
            "model_snapshot_ref": snapshot.model_dump(mode="json"), "prompt_version": "fixture-v1",
            "resources": UIResourceLimits().model_dump(mode="json"), "result_roles": ["mask", "alpha"],
        }
        if purpose == "inpaint":
            request_content = json.loads(FIXTURE.read_text())["masked_plan"]
            request_content["task_id"] = task_id
            request_content["envelope"]["budget"] = BUDGET if budget is None else budget
            mask = BytesIO()
            Image.new("L", (64, 48), 255).save(mask, format="PNG")
            binding = request_content["image_mask"]
            for field, role, content in (
                ("canonical_ref", "canonical", stream.getvalue()),
                ("image_ref", "image", stream.getvalue()),
                ("mask_ref", "edit_mask", mask.getvalue()),
                ("canonical_edit_mask_ref", "edit_mask", mask.getvalue()),
                ("selection_ref", "selection", canonical_json(value).encode()),
                ("view_transform_ref", "view_transform", b'{"crop":[0,0,64,48],"resize":[64,48],"pad":[0,0,0,0]}'),
            ):
                binding[field] = service.artifacts.put(task_id, "input", content, role=role).model_dump(mode="json")
            binding["selection_hash"] = binding["selection_ref"]["sha256"]
            binding["selection_revision"] = revision
        request_ref = service.artifacts.put(
            task_id, "request", canonical_json(request_content).encode(), role="request",
        )
        return service.ui_child_plan(
            task_id, parent_task_id=parent_task_id, purpose=purpose,
            source_ids=[image.artifact_id] if source_ids is None else source_ids,
            request_ref=request_ref, selection_ref=selection_ref,
            selection_revision=revision, budget=BUDGET if budget is None else budget,
        )

    return service, root, child


def reserve(service, plan, cost=0.2, revision=0):
    capability = "ui.segment" if plan.workflow_type == "ui_segmentation" else "ui.inpaint"
    request_ref = ArtifactRef.model_validate(plan.parameters["request_ref"])
    request = json.loads(service.artifacts.read(request_ref))
    selection = request["image_mask"] if plan.workflow_type == "ui_inpaint" else request
    binding = UIStepBinding(
        task_id=plan.task_id, step_id="gpu", revision=revision, capability=capability, inputs=plan.inputs,
        selection_ref=selection["selection_ref"], selection_revision=selection["selection_revision"],
        selection_hash=selection["selection_hash"],
    )
    return service.ledger.reserve(plan, "gpu", revision, Cost(cost_usd=cost, gpu_minutes=1), ui_binding=binding)


def test_root_analysis_approval_does_not_authorize_segmentation(family):
    service, root, child = family
    plan = child("segment")
    assert service.ledger.plan(root.task_id).fingerprint == root.fingerprint
    with pytest.raises(PipelineError) as caught:
        reserve(service, plan)
    assert caught.value.code in {"approval_required", "not_approved"}
    assert service.ledger.list_operations(plan.task_id) == []
    authorize(service, plan)
    assert reserve(service, plan).state == "prepared"


def test_segmentation_approval_does_not_authorize_final_mask_generation(family):
    service, _, child = family
    segmentation = child("segment")
    authorize(service, segmentation)
    inpaint = child("inpaint", purpose="inpaint")
    assert inpaint.fingerprint != segmentation.fingerprint
    with pytest.raises(PipelineError) as caught:
        reserve(service, inpaint)
    assert caught.value.code in {"approval_required", "not_approved"}
    assert service.ledger.list_operations(inpaint.task_id) == []


@pytest.mark.parametrize("overrides", [
    {"source_ids": ["foreign-source"]},
    {"budget": {**BUDGET, "max_total_cost_usd": 2}},
    {"budget": {**BUDGET, "max_iteration_gpu_minutes": 6}},
    {"budget": {**BUDGET, "max_revisions": 3}},
    {"parent_task_id": "missing-parent"},
    {"parent_task_id": "child"},
])
def test_child_cannot_expand_sources_budget_or_create_parent_cycle(family, overrides):
    service, root, child = family
    with pytest.raises((PipelineError, ValueError)):
        child("child", **overrides)
    assert service.ledger.list_operations(root.task_id) == []
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM ui_child_bindings").fetchone()[0] == 0


def test_exact_new_selection_approval_supersedes_old_pending_child(family):
    service, root, child = family
    old = child("old")
    authorize(service, old)
    new = child("new", revision=1)
    assert new.fingerprint != old.fingerprint
    authorize(service, new)
    with pytest.raises(PipelineError) as caught:
        reserve(service, old)
    assert caught.value.code == "selection_superseded"
    assert service.ledger.plan(old.task_id) == old
    assert service.ledger.plan(root.task_id) == root
    assert reserve(service, new).state == "prepared"


def test_replaying_superseded_approval_cannot_reactivate_old_selection(family):
    service, _, child = family
    old = child("old")
    old_approval = approve(service.ledger, old.task_id, old.fingerprint)
    service.ledger.consume_approval(old_approval)
    new = child("new", revision=1)
    authorize(service, new)
    with pytest.raises(PipelineError) as caught:
        service.ledger.consume_approval(old_approval)
    assert caught.value.code == "selection_superseded"
    assert reserve(service, new).state == "prepared"


def test_actual_sum_across_children_closes_root_gate_on_overrun(family):
    service, _, child = family
    first = child("first")
    authorize(service, first)
    op = reserve(service, first, cost=0.4)
    service.ledger.finish(op.operation_id, Cost(cost_usd=0.6, gpu_minutes=1), {})
    second = child("second", revision=1)
    authorize(service, second)
    op = reserve(service, second, cost=0.3)
    result = service.ledger.finish(op.operation_id, Cost(cost_usd=0.5, gpu_minutes=1), {})
    assert result.state == "failed"
    assert result.result["usage_verdict"] == "budget_exceeded"
    with pytest.raises(PipelineError):
        third = child("third", revision=2)
        authorize(service, third)


@pytest.mark.parametrize("state", ["submitted", "outcome_unknown"])
def test_new_selection_cannot_bypass_inflight_or_unknown_gpu(family, state):
    service, root, child = family
    old = child("old")
    authorize(service, old)
    operation = reserve(service, old)
    # Persist an external-acceptance boundary without calling any provider.
    with service.ledger.transaction() as db:
        db.execute("UPDATE operations SET state=?,provider_request_id=? WHERE operation_id=?",
                   (state, "synthetic-provider-request", operation.operation_id))
    before = service.ledger.usage(root.task_id, include_children=True)
    new = child("new", revision=1)
    with pytest.raises(PipelineError) as caught:
        authorize(service, new)
    assert caught.value.code == "awaiting_reconciliation"
    assert service.ledger.usage(root.task_id, include_children=True) == before
    assert service.ledger.get(operation.operation_id).provider_request_id == "synthetic-provider-request"


def test_children_share_actual_reserved_and_unsettled_without_double_charge(family):
    service, root, child = family
    first = child("first")
    authorize(service, first)
    operation = reserve(service, first, 0.2)
    service.ledger.finish(operation.operation_id, Cost(cost_usd=0.4, gpu_minutes=1), {})
    second = child("second", revision=1)
    authorize(service, second)
    reserve(service, second, 0.3)
    usage = service.ledger.usage(root.task_id, include_children=True)
    assert usage == {"actual": Cost(cost_usd=0.4, gpu_minutes=1),
                     "reserved": Cost(cost_usd=0.3, gpu_minutes=1), "unsettled": Cost()}
    with service.ledger.transaction() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(ui_operation_charges)")}
        assert not columns & {"actual_cost", "reserved_cost", "actual_gpu", "reserved_gpu"}
        assert db.execute("SELECT COUNT(DISTINCT operation_id) FROM ui_operation_charges").fetchone()[0] == 2
    assert service.ledger.plan(first.task_id) == first


def test_new_child_cannot_reset_consumed_root_budget(family):
    service, root, child = family
    for i in range(2):
        plan = child(f"spent-{i}", revision=i)
        authorize(service, plan)
        operation = reserve(service, plan, 0.4)
        service.ledger.finish(operation.operation_id, Cost(cost_usd=0.4, gpu_minutes=1), {})
    third = child("third", revision=2)
    authorize(service, third)
    with pytest.raises(PipelineError) as caught:
        reserve(service, third, 0.3)
    assert caught.value.code == "budget_insufficient"
    assert service.ledger.usage(root.task_id, include_children=True)["actual"].cost_usd == 0.8


def test_root_cancel_closes_existing_child_submission(family):
    service, root, child = family
    plan = child("prepared")
    authorize(service, plan)
    operation = reserve(service, plan)
    service.ledger.request_cancel(root.task_id)
    with pytest.raises(PipelineError) as caught:
        service.ledger.begin_submit(operation.operation_id)
    assert caught.value.code == "cancelled"


def test_replacement_children_share_generation_revision_limit(family):
    service, _, child = family
    for index in range(2):
        plan = child(f"revision-{index}", revision=index, purpose="inpaint")
        authorize(service, plan)
        operation = reserve(service, plan, cost=0, revision=1)
        service.ledger.finish(operation.operation_id, Cost(gpu_minutes=1), {})
    extra = child("revision-extra", revision=2, purpose="inpaint")
    authorize(service, extra)
    with pytest.raises(PipelineError) as caught:
        reserve(service, extra, cost=0, revision=1)
    assert caught.value.code in {"revision_limit", "call_limit"}
