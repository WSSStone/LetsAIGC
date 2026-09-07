"""Failed local model calls retain their root call allowance."""

import pytest
from test_ui_local_revision_budget import _authorize, _binding, _local_child, _root

from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.pipeline import ArtifactRef, Cost
from letsaigc.schemas.ui import UIAnalysisRequest


def _request(service, root):
    return UIAnalysisRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(root.parameters["request_ref"]))
    )


def test_failed_ocr_child_cannot_reset_call_limit_with_another_task_id(tmp_path):
    service, root, original, raw, selection = _root(tmp_path, ocr_rereads=1)
    child = _local_child(service, root, original, raw, selection, "first", "reread_text")
    _authorize(service, child)
    operation = service.ledger.reserve(child, "ocr", 0, Cost(),
                                       ui_binding=_binding(child, "ui.ocr", "ocr"),
                                       ui_request=_request(service, root))
    service.ledger.begin_submit(operation.operation_id)
    service.ledger.finish_ui(operation.operation_id, Cost(), {"artifacts": []}, failed=True)
    replacement = _local_child(service, root, original, raw, selection, "second", "reread_text")
    _authorize(service, replacement)
    with pytest.raises(PipelineError) as caught:
        service.ledger.reserve(replacement, "ocr", 0, Cost(),
                               ui_binding=_binding(replacement, "ui.ocr", "ocr"),
                               ui_request=_request(service, root))
    assert caught.value.code == "call_limit"
    assert len(service.ledger.list_operations(child.task_id)) == 1
    assert not service.ledger.list_operations(replacement.task_id)


def test_local_child_cannot_present_a_looser_root_call_policy(tmp_path):
    service, root, original, raw, selection = _root(tmp_path, ocr_rereads=1)
    child = _local_child(service, root, original, raw, selection, "revision", "reread_text")
    _authorize(service, child)
    request = _request(service, root)
    forged = request.model_copy(update={"limits": request.limits.model_copy(update={"ocr_rereads_per_image": 2})})
    with pytest.raises(PipelineError) as caught:
        service.ledger.reserve(child, "ocr", 0, Cost(),
                               ui_binding=_binding(child, "ui.ocr", "ocr"), ui_request=forged)
    assert caught.value.code == "input_changed"
    assert not service.ledger.list_operations(child.task_id)


def test_disabled_local_vlm_views_cannot_spend_unused_global_calls(tmp_path):
    service, root, original, raw, selection = _root(tmp_path, vlm_local_views=0)
    child = _local_child(service, root, original, raw, selection, "revision", "review_region")
    _authorize(service, child)
    with pytest.raises(PipelineError) as caught:
        service.ledger.reserve(child, "analyze", 0, Cost(),
                               ui_binding=_binding(child, "ui.analyze", "analyze"),
                               ui_request=_request(service, root))
    assert caught.value.code == "call_limit"
    assert not service.ledger.list_operations(child.task_id)
