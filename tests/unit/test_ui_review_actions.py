import copy

import pytest

from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.ui_review import ReviewDocument, ReviewPatch
from letsaigc.ui_analysis.review import adapt_layout, apply_actions


def sample():
    return ReviewDocument(
        task_id="review-test",
        source_id="source-1",
        width=100,
        height=80,
        elements=[
            {"element_id": "panel", "base_type": "container", "bbox": [0, 0, 90, 70]},
            {
                "element_id": "label",
                "base_type": "text",
                "bbox": [10, 10, 60, 30],
                "parent_id": "panel",
                "text_region_ids": ["ocr-1"],
            },
        ],
        texts=[
            {
                "text_region_id": "ocr-1",
                "effective_text": "错",
                "bbox": [10, 10, 60, 30],
                "origin": "ocr",
                "ocr_text_id": "ocr-1",
                "original_text": "错",
                "original_score": 0.6,
            }
        ],
    )


def change(doc, *actions):
    return apply_actions(doc, ReviewPatch(request_id="change-1", actions=list(actions)))[0]


def test_human_text_and_geometry_preserve_raw_ocr_and_identity():
    doc = sample()
    changed = change(
        doc,
        {"action": "set_text", "text_region_id": "ocr-1", "text": "正确"},
        {"action": "update_box", "element_id": "label", "bbox": [9, 9, 65, 31]},
    )
    assert changed.texts[0].effective_text == "正确"
    assert changed.texts[0].original_text == doc.texts[0].original_text == "错"
    assert changed.texts[0].original_score == 0.6
    assert changed.texts[0].origin == "human"
    assert changed.elements[1].element_id == "label"
    assert doc.elements[1].bbox == [10, 10, 60, 30]


@pytest.mark.parametrize(
    "action",
    [
        {"action": "update_box", "element_id": "label", "bbox": [-1, 0, 5, 5]},
        {"action": "update_box", "element_id": "label", "bbox": [2, 2, 2, 5]},
        {"action": "update_box", "element_id": "label", "bbox": [0, 0, 101, 5]},
        {"action": "set_parent", "element_id": "panel", "parent_id": "label"},
        {"action": "set_parent", "element_id": "label", "parent_id": "missing"},
        {"action": "set_text_links", "element_id": "label", "text_region_ids": ["foreign"]},
        {"action": "delete_region", "element_id": "panel"},
    ],
)
def test_invalid_geometry_and_relationships_leave_original_unchanged(action):
    doc = sample()
    before = doc.model_dump()
    with pytest.raises((PipelineError, ValueError)):
        change(doc, action)
    assert doc.model_dump() == before


def test_locks_require_explicit_unlock_and_deleting_parent_requires_policy():
    locked = change(sample(), {"action": "set_lock", "element_id": "label", "fields": ["bbox"]})
    with pytest.raises(PipelineError) as error:
        change(locked, {"action": "update_box", "element_id": "label", "bbox": [1, 1, 10, 10]})
    assert error.value.code == "review_locked"
    detached = change(locked, {"action": "delete_region", "element_id": "panel", "children": "detach_children"})
    assert len(detached.elements) == 1 and detached.elements[0].parent_id is None


def test_added_human_text_has_no_fake_ocr_and_is_repeatable():
    action = {
        "action": "add_region",
        "element_id": "temp-new",
        "base_type": "text",
        "bbox": [1, 1, 8, 8],
        "text": "人工",
    }
    a = change(sample(), action)
    b = change(sample(), action)
    assert a == b and len(a.elements) == 3
    assert a.texts[-1].origin == "human" and a.texts[-1].ocr_text_id is None
    assert a.texts[-1].original_score is None


def test_v1_adaptation_preserves_old_kind_ids_and_input():
    layout = {
        "schema_version": 1,
        "source_id": "source-1",
        "width": 100,
        "height": 80,
        "elements": [
            {"element_id": "icon-1", "kind": "icon", "bbox": [1, 2, 10, 20], "parent_id": None, "text_ids": []}
        ],
    }
    before = copy.deepcopy(layout)
    adapted = adapt_layout("review-test", layout, {"texts": []})
    assert adapted.schema_version == 2
    assert adapted.elements[0].semantic_tags == ["icon"]
    assert adapted.elements[0].element_id == "icon-1" and layout == before
    with pytest.raises(PipelineError):
        adapt_layout("review-test", {**layout, "schema_version": 999}, {"texts": []})


def test_temporary_human_text_can_be_linked_without_ocr_identity():
    doc = sample()
    changed, mapping = apply_actions(
        doc,
        ReviewPatch(
            request_id="new-text-link",
            actions=[
                {
                    "action": "add_region",
                    "element_id": "temp-word",
                    "base_type": "text",
                    "bbox": [1, 1, 8, 8],
                    "text": "新",
                },
                {"action": "set_text_links", "element_id": "panel", "text_region_ids": ["temp-word-text"]},
                {"action": "set_text", "text_region_id": "temp-word-text", "text": "新文字"},
            ],
        ),
    )
    assert changed.elements[0].text_region_ids == [mapping["temp-word-text"]]
    assert changed.texts[-1].effective_text == "新文字" and changed.texts[-1].ocr_text_id is None
