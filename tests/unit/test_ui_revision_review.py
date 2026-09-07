"""Local acceptance tests for immutable model review suggestions."""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.ui_review import ReviewConfirm, ReviewDocument, ReviewPatch
from letsaigc.ui_analysis.review import ReviewRepository
from letsaigc.ui_analysis.revision_review import accept_suggestion, save_suggestion


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (16, 12), "white").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def reviewed(tmp_path):
    service = PipelineService(tmp_path / "domain", ui_schema=5)
    service.smoke_plan("review-task")
    canonical = service.artifacts.put(
        "review-task", "input", _png(), role="canonical", media_type="image/png"
    )
    evidence = service.artifacts.put("review-task", "evidence", b"model-evidence", role="model_evidence")
    document = ReviewDocument(
        task_id="review-task",
        source_id="source-1",
        width=16,
        height=12,
        elements=[
            {
                "element_id": "panel",
                "base_type": "container",
                "bbox": [0, 0, 16, 12],
                "field_sources": {"bbox": "model"},
            },
            {
                "element_id": "label",
                "base_type": "text",
                "bbox": [2, 2, 8, 6],
                "text_region_ids": ["ocr-1"],
                "field_sources": {"bbox": "model"},
            },
        ],
        texts=[
            {
                "text_region_id": "ocr-1",
                "effective_text": "原文",
                "bbox": [2, 2, 8, 6],
                "origin": "ocr",
                "ocr_text_id": "ocr-1",
                "original_text": "原文",
                "original_score": 0.9,
            }
        ],
    )
    repository = ReviewRepository(service.ledger, service.artifacts)
    repository.initialize(document, {"canonical_ref": canonical.model_dump(mode="json")})
    repository.confirm(
        "review-task",
        ReviewConfirm(request_id="confirm-0", draft_revision=0, expected_confirmed_revision=None),
    )
    return service, repository, repository.binding("review-task", 0), evidence


def test_save_suggestion_is_immutable_and_does_not_change_review_heads(reviewed):
    service, repository, binding, evidence = reviewed
    before = repository.head("review-task")

    suggestion = save_suggestion(
        service,
        binding,
        ["label"],
        [{"action": "update_box", "element_id": "label", "bbox": [3, 3, 9, 7]}],
        [evidence],
    )

    assert suggestion.role == "review_suggestion"
    assert repository.head("review-task") == before
    payload = json.loads(service.artifacts.read(suggestion))
    assert payload["review_revision"] == binding.review_revision == 0
    assert payload["review_hash"] == binding.review_manifest_ref.sha256
    assert payload["binding_hash"]
    assert payload["base_draft_revision"] == 0
    assert payload["base_confirmed_revision"] == 0
    assert payload["target_ids"] == ["label"]
    assert payload["evidence_refs"][0]["artifact_id"] == evidence.artifact_id
    assert repository.document("review-task", 0) == repository.document("review-task", 0)


def test_accept_suggestion_requires_explicit_call_and_creates_human_revision(reviewed):
    service, repository, binding, evidence = reviewed
    suggestion = save_suggestion(
        service,
        binding,
        ["label"],
        [{"action": "update_box", "element_id": "label", "bbox": [3, 3, 9, 7]}],
        [evidence],
    )
    assert repository.head("review-task")["draft_revision"] == 0

    result = accept_suggestion(
        service,
        suggestion,
        "operator-accept-1",
        base_draft_revision=0,
        base_confirmed_revision=0,
    )

    assert result["state"] == "accepted"
    assert result["revision"] == 1
    assert repository.head("review-task")["confirmed_revision"] == 0
    assert repository.document("review-task", 1).elements[1].bbox == [3, 3, 9, 7]
    assert repository.document("review-task", 1).elements[1].field_sources["bbox"] == "human"
    repeated = accept_suggestion(service, suggestion, "operator-accept-1",
                                 base_draft_revision=0, base_confirmed_revision=0)
    assert repeated == result
    assert repository.head("review-task")["draft_revision"] == 1


def test_human_origin_is_not_a_save_barrier_but_locked_fields_are(reviewed):
    service, _, _, _ = reviewed
    service2, repository2, binding2, evidence2 = _fresh_review(service, human_text=True)
    suggestion = save_suggestion(
        service2,
        binding2,
        ["label", "ocr-1"],
        [{"action": "set_text", "text_region_id": "ocr-1", "text": "模型建议"}],
        [evidence2],
    )
    assert suggestion.role == "review_suggestion"
    assert repository2.document("review-task-2", 0).texts[0].origin == "human"


def _fresh_review(service, *, human_text=False):
    """Create an independent v5 repository beside the first fixture."""

    # The fixture service root can host another task without any migration or
    # provider work; this keeps the test focused on review artifacts.
    task = "review-task-2"
    service.smoke_plan(task)
    canonical = service.artifacts.put(task, "input", _png(), role="canonical", media_type="image/png")
    evidence = service.artifacts.put(task, "evidence", b"model-evidence", role="model_evidence")
    text = {
        "text_region_id": "ocr-1",
        "effective_text": "原文",
        "bbox": [2, 2, 8, 6],
        "origin": "human" if human_text else "ocr",
    }
    if not human_text:
        text.update(ocr_text_id="ocr-1", original_text="原文", original_score=0.9)
    document = ReviewDocument(
        task_id=task,
        source_id="source-1",
        width=16,
        height=12,
        elements=[{"element_id": "label", "base_type": "text", "bbox": [2, 2, 8, 6]}],
        texts=[text],
    )
    repository = ReviewRepository(service.ledger, service.artifacts)
    repository.initialize(document, {"canonical_ref": canonical.model_dump(mode="json")})
    repository.confirm(
        task,
        ReviewConfirm(request_id="confirm-2", draft_revision=0, expected_confirmed_revision=None),
    )
    return service, repository, repository.binding(task, 0), evidence


def test_locked_field_suggestion_is_rejected_without_writing(reviewed):
    service, _, _, _ = reviewed
    locked_document = ReviewDocument(
        task_id="review-task-locked",
        source_id="source-1",
        width=16,
        height=12,
        elements=[
            {
                "element_id": "label",
                "base_type": "text",
                "bbox": [2, 2, 8, 6],
                "locked_fields": ["bbox"],
            }
        ],
    )
    # Build an independent review through the same v5 repository APIs; the
    # binding itself remains immutable and task-scoped.
    service.smoke_plan("review-task-locked")
    repository = ReviewRepository(service.ledger, service.artifacts)
    canonical = service.artifacts.put(
        "review-task-locked", "input", _png(), role="canonical", media_type="image/png"
    )
    repository.initialize(locked_document, {"canonical_ref": canonical.model_dump(mode="json")})
    repository.confirm(
        "review-task-locked",
        ReviewConfirm(request_id="confirm-locked", draft_revision=0, expected_confirmed_revision=None),
    )
    locked_binding = repository.binding("review-task-locked", 0)
    locked_evidence = service.artifacts.put("review-task-locked", "evidence", b"e", role="model_evidence")
    with pytest.raises(PipelineError) as raised:
        save_suggestion(
            service,
            locked_binding,
            ["label"],
            [{"action": "update_box", "element_id": "label", "bbox": [3, 3, 9, 7]}],
            [locked_evidence],
        )
    assert raised.value.code == "review_locked"
    assert repository.head("review-task-locked")["draft_revision"] == 0


@pytest.mark.parametrize(
    "action",
    [
        {"action": "add_region", "element_id": "new", "base_type": "image", "bbox": [1, 1, 4, 4]},
        {"action": "delete_region", "element_id": "label", "children": "detach_children"},
        {"action": "set_lock", "element_id": "label", "fields": ["bbox"]},
        {"action": "restore_revision", "revision": 0},
    ],
)
def test_suggestion_action_allowlist_rejects_identity_lock_and_restore_changes(reviewed, action):
    service, _, binding, evidence = reviewed
    with pytest.raises(PipelineError) as raised:
        save_suggestion(service, binding, ["label"], [action], [evidence])
    assert raised.value.code == "review_suggestion_invalid"


def test_suggestion_target_scope_and_foreign_evidence_are_rejected(reviewed):
    service, _, binding, evidence = reviewed
    with pytest.raises(PipelineError) as raised:
        save_suggestion(
            service,
            binding,
            ["label", "ocr-1"],
            [{"action": "update_box", "element_id": "panel", "bbox": [1, 1, 8, 8]}],
            [evidence],
        )
    assert raised.value.code == "review_target_scope"

    foreign = service.artifacts.put("foreign-task", "evidence", b"foreign", role="model_evidence")
    with pytest.raises(PipelineError) as raised:
        save_suggestion(
            service,
            binding,
            ["label", "ocr-1"],
            [{"action": "set_text", "text_region_id": "ocr-1", "text": "建议"}],
            [foreign],
        )
    assert raised.value.code == "artifact_scope"

    suggestion = save_suggestion(
        service,
        binding,
        ["label", "ocr-1"],
        [{"action": "set_text", "text_region_id": "ocr-1", "text": "建议"}],
        [foreign],
        evidence_task_ids=["review-task", "foreign-task"],
    )
    assert suggestion.role == "review_suggestion"


def test_accept_rejects_stale_base_heads(reviewed):
    service, repository, binding, evidence = reviewed
    suggestion = save_suggestion(
        service,
        binding,
        ["label"],
        [{"action": "update_box", "element_id": "label", "bbox": [3, 3, 9, 7]}],
        [evidence],
    )
    repository.save(
        "review-task",
        ReviewPatch(
            request_id="newer-draft",
            base_draft_revision=0,
            base_confirmed_revision=0,
            actions=[],
        ),
    )
    with pytest.raises(PipelineError) as raised:
        accept_suggestion(
            service,
            suggestion,
            "operator-stale",
            base_draft_revision=0,
            base_confirmed_revision=0,
        )
    assert raised.value.code == "review_conflict"
