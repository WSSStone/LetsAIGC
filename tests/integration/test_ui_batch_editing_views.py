"""CLI editing status uses the actual batch image owner."""

from test_ui_batch import family, run_child
from test_ui_batch_editing_budget import segment
from test_ui_child_approvals import authorize

from letsaigc.schemas.pipeline import Cost
from letsaigc.ui_analysis.cli import _editing_view
from letsaigc.ui_analysis.editing import EditingExecution


def test_cli_image_view_includes_pending_gpu_descendant(tmp_path):
    service, root, batch = family(tmp_path)
    image = batch.prepare(root).child
    child = segment(service, image, "pending-cli-gpu")
    view = _editing_view(service, image)
    assert [item["task_id"] for item in view["pending_approvals"]] == [child.task_id]
    assert [item["task_id"] for item in view["children"]] == [child.task_id]
    assert view["children"][0]["status"] == "pending"


def test_cli_image_view_does_not_mix_previous_image_children(tmp_path):
    service, root, batch = family(tmp_path)
    first_image = batch.prepare(root).child
    previous = segment(service, first_image, "previous-gpu")
    authorize(service, previous)
    binding = EditingExecution(service).binding(previous)
    operation = service.ledger.reserve(previous, binding.step_id, 0, Cost(), ui_binding=binding)
    service.ledger.finish(operation.operation_id, Cost(), {})
    batch.complete(root, run_child(service, first_image))
    second_image = batch.prepare(root).child
    current = segment(service, second_image, "current-gpu")
    assert [item["task_id"] for item in _editing_view(service, second_image)["children"]] == [current.task_id]
    assert [item["task_id"] for item in _editing_view(service, first_image)["children"]] == [previous.task_id]
