"""Human corrections cannot manufacture model evidence or execution authority."""

import pytest
from pydantic import ValidationError

from letsaigc.schemas.ui_review import ReviewPatch


def test_review_patch_is_closed_and_has_independent_revision_numbers():
    patch = ReviewPatch(request_id="save-1", base_draft_revision=21, actions=[])
    assert patch.base_draft_revision == 21
    for extra in ({"approve": True}, {"model": "anything"}, {"actor": "model"}):
        with pytest.raises(ValidationError):
            ReviewPatch(request_id="save-1", actions=[], **extra)


@pytest.mark.parametrize(
    "action",
    [
        {"action": "execute"},
        {"action": "update_box", "element_id": "item", "bbox": [0, 0, True, 10]},
        {"action": "update_box", "element_id": "item", "bbox": [0, 0, 10.5, 10]},
        {"action": "set_text", "text_region_id": "word", "text": "correct", "score": 1},
        {"action": "set_type", "element_id": "item", "base_type": "live2d"},
    ],
)
def test_invalid_actions_are_rejected_before_writes(action):
    with pytest.raises(ValidationError):
        ReviewPatch(request_id="save-1", actions=[action])


def test_limits_are_not_unbounded():
    with pytest.raises(ValidationError):
        ReviewPatch(request_id="many", actions=[{"action": "set_lock", "element_id": "x", "fields": []}] * 257)


def test_repository_cas_idempotency_and_scope(tmp_path):
    from letsaigc.pipelines.errors import PipelineError
    from letsaigc.pipelines.service import PipelineService
    from letsaigc.schemas.ui_review import ReviewDocument
    from letsaigc.ui_analysis.review import ReviewRepository

    service = PipelineService(tmp_path, ui_schema=4)
    service.smoke_plan("review-test")
    document = ReviewDocument(task_id="review-test", source_id="source", width=10, height=10)
    base = service.artifacts.put("review-test", "base", b"{}", role="layout")
    repo = ReviewRepository(service.ledger, service.artifacts)
    repo.initialize(document, {"base_layout_ref": base.model_dump(mode="json")})
    patch = ReviewPatch(
        request_id="once",
        actions=[{"action": "add_region", "element_id": "temp", "base_type": "image", "bbox": [0, 0, 5, 5]}],
    )
    saved = repo.save("review-test", patch)
    assert saved["revision"] == 1 and repo.save("review-test", patch) == saved
    with pytest.raises(PipelineError) as error:
        repo.save("review-test", ReviewPatch(request_id="once", actions=[]))
    assert error.value.code == "review_request_conflict"
    with pytest.raises(PipelineError) as error:
        repo.save("review-test", ReviewPatch(request_id="stale", actions=[]))
    assert error.value.code == "review_conflict"
    assert repo.request("review-test", "stale")["state"] == "conflict"
    assert repo.head("review-test")["draft_revision"] == 1
    assert repo.document("review-test", 0) == document
    assert ReviewRepository(service.ledger, service.artifacts).document("review-test", 1) != document
    with pytest.raises(PipelineError):
        repo.document("foreign", 1)
    assert not service.ledger.list_operations("review-test")


def test_future_ledger_reader_is_rejected(tmp_path):
    import sqlite3

    from letsaigc.pipelines.errors import PipelineError
    from letsaigc.pipelines.ledger import Ledger

    path = tmp_path / "future.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=6")
    with pytest.raises(PipelineError) as error:
        Ledger(path)
    assert error.value.code == "ledger_version"
