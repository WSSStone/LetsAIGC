import json
from io import BytesIO

import pytest
from PIL import Image

from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef
from letsaigc.schemas.ui_review import ReviewConfirm, ReviewDocument, ReviewPatch
from letsaigc.ui_analysis.normalize import png
from letsaigc.ui_analysis.review import ReviewRepository


def fixture_repo(tmp_path):
    service = PipelineService(tmp_path, ui_schema=4)
    service.smoke_plan("review-test")
    repo = ReviewRepository(service.ledger, service.artifacts)
    ref = service.artifacts.put(
        "review-test", "canonical", png(Image.new("RGB", (20, 20), "red")), role="canonical", media_type="image/png"
    )
    doc = ReviewDocument(
        task_id="review-test",
        source_id="source",
        width=20,
        height=20,
        elements=[{"element_id": "box", "base_type": "image", "bbox": [1, 2, 10, 11]}],
    )
    repo.initialize(doc, {"canonical_ref": ref.model_dump(mode="json")})
    return repo


def test_confirm_preserves_pixels_and_reuses_unchanged_crop(tmp_path):
    repo = fixture_repo(tmp_path)
    request = ReviewConfirm(request_id="confirm-0", draft_revision=0)
    first = repo.confirm("review-test", request)
    assert first == repo.confirm("review-test", request)
    manifest = json.loads(repo.store.read(ArtifactRef.model_validate(first["manifest_ref"])))
    crop = ArtifactRef.model_validate(manifest["crops"][0]["ref"])
    with Image.open(BytesIO(repo.store.read(crop))) as image:
        assert image.size == (9, 9) and image.getpixel((0, 0)) == (255, 0, 0)
    repo.save(
        "review-test",
        ReviewPatch(
            request_id="type",
            base_confirmed_revision=0,
            actions=[{"action": "set_type", "element_id": "box", "base_type": "container"}],
        ),
    )
    second = repo.confirm(
        "review-test", ReviewConfirm(request_id="confirm-1", draft_revision=1, expected_confirmed_revision=0)
    )
    updated = json.loads(repo.store.read(ArtifactRef.model_validate(second["manifest_ref"])))
    assert updated["crops"][0]["ref"] == manifest["crops"][0]["ref"]
    assert updated["external_calls"] == 0 and updated["evaluation_lane"] == "human_assisted"
    assert not repo.ledger.list_operations("review-test")


def test_confirmation_failure_and_process_exit_do_not_publish(tmp_path, monkeypatch):
    from letsaigc.ui_analysis import review_projection

    repo = fixture_repo(tmp_path)
    original = review_projection.materialize
    request = ReviewConfirm(request_id="interrupted", draft_revision=0)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(review_projection, "materialize", interrupt)
    with pytest.raises(KeyboardInterrupt):
        repo.confirm("review-test", request)
    assert repo.head("review-test")["confirmed_revision"] is None
    assert repo.request("review-test", "interrupted")["state"] == "confirming"
    monkeypatch.setattr(review_projection, "materialize", original)
    assert repo.confirm("review-test", request)["state"] == "confirmed"


def test_storage_failure_and_conflicting_publish_preserve_head(tmp_path, monkeypatch):
    from letsaigc.ui_analysis import review_projection

    repo = fixture_repo(tmp_path)
    original = review_projection.materialize

    def fail(*args, **kwargs):
        raise PipelineError("review_storage_insufficient")

    monkeypatch.setattr(review_projection, "materialize", fail)
    with pytest.raises(PipelineError):
        repo.confirm("review-test", ReviewConfirm(request_id="full", draft_revision=0))
    assert repo.request("review-test", "full")["state"] == "failed"
    assert repo.head("review-test")["confirmed_revision"] is None

    def concurrent(*args, **kwargs):
        result = original(*args, **kwargs)
        repo.save("review-test", ReviewPatch(request_id="other-tab", actions=[]))
        return result

    monkeypatch.setattr(review_projection, "materialize", concurrent)
    with pytest.raises(PipelineError) as error:
        repo.confirm("review-test", ReviewConfirm(request_id="conflict", draft_revision=0))
    assert error.value.code == "review_conflict"
    assert repo.head("review-test")["confirmed_revision"] is None


def test_confirmed_snapshot_is_immutable_across_new_confirmation_ids(tmp_path):
    repo = fixture_repo(tmp_path)
    first = repo.confirm("review-test", ReviewConfirm(request_id="first", draft_revision=0))
    again = repo.confirm(
        "review-test", ReviewConfirm(request_id="again", draft_revision=0, expected_confirmed_revision=0)
    )
    assert again["manifest_ref"] == first["manifest_ref"]


def test_failed_save_records_failure_and_keeps_original_draft(tmp_path, monkeypatch):
    repo = fixture_repo(tmp_path)
    original = repo.put

    def fail(*args, **kwargs):
        raise PipelineError("review_storage_insufficient")

    monkeypatch.setattr(repo, "put", fail)
    request = ReviewPatch(request_id="disk-full-save", actions=[])
    with pytest.raises(PipelineError):
        repo.save("review-test", request)
    assert repo.head("review-test")["draft_revision"] == 0
    assert repo.request("review-test", "disk-full-save")["state"] == "failed"
    monkeypatch.setattr(repo, "put", original)
    assert repo.save("review-test", request)["revision"] == 1
