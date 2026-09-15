"""Cancellation must retain every editing descendant until external work settles."""

from test_ui_batch import family
from test_ui_batch_editing_budget import reserve, segment
from test_ui_child_approvals import authorize

from letsaigc.schemas.pipeline import Cost


def test_batch_cancel_settles_prepared_gpu_grandchild_before_releasing_image(tmp_path):
    service, root, batch = family(tmp_path)
    image = batch.prepare(root).child
    gpu = segment(service, image, "gpu")
    authorize(service, gpu)
    operation = reserve(service, gpu, cost=0.1)
    assert batch.cancel(root) is True
    assert service.ledger.get(operation.operation_id).state == "failed"
    assert service.ledger.usage(root.task_id, include_children=True)["reserved"] == Cost()
    with service.ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM ui_child_bindings WHERE active=1").fetchone()[0] == 0


def test_batch_cancel_retains_unknown_gpu_grandchild_and_image_ownership(tmp_path):
    service, root, batch = family(tmp_path)
    image = batch.prepare(root).child
    gpu = segment(service, image, "gpu")
    authorize(service, gpu)
    operation = reserve(service, gpu, cost=0.1)
    service.ledger.begin_submit(operation.operation_id)
    service.ledger.uncertain(operation.operation_id)
    assert batch.cancel(root) is False
    assert service.ledger.usage(root.task_id, include_children=True)["unsettled"] == Cost(cost_usd=0.1)
    assert batch.status(root).state == "awaiting_reconciliation"
    with service.ledger.transaction() as db:
        assert db.execute("SELECT active FROM ui_child_bindings WHERE task_id=?", (image.task_id,)).fetchone()[0] == 1


def test_cancel_before_provision_does_not_show_planned_or_pending_approval(tmp_path):
    service, root, batch = family(tmp_path)
    assert batch.cancel(root) is True
    assert batch.status(root).state == "cancelled"
    assert batch.status(root).child is None
    assert service.ledger.list_operations(root.task_id) == []


def test_batch_completion_cannot_skip_an_unsettled_deep_descendant(tmp_path):
    import pytest
    from test_ui_batch import run_child

    from letsaigc.pipelines.errors import PipelineError

    service, root, batch = family(tmp_path)
    image = batch.prepare(root).child
    gpu = segment(service, image, "gpu")
    authorize(service, gpu)
    service.ledger.finish(reserve(service, gpu).operation_id, Cost(), {})
    result = run_child(service, image)
    deep = segment(service, image, "deep", parent_task_id=gpu.task_id)
    authorize(service, deep)
    operation = reserve(service, deep, cost=0.1)
    service.ledger.begin_submit(operation.operation_id)
    service.ledger.uncertain(operation.operation_id)
    with pytest.raises(PipelineError) as caught:
        batch.complete(root, result)
    assert caught.value.code == "awaiting_reconciliation"
