from letsaigc.schemas.ui_review import ReviewDocument, ReviewPatch
from letsaigc.ui_analysis.review import apply_actions


def test_text_geometry_provenance_and_parent_delete_dependency_closure():
    doc = ReviewDocument(
        task_id="closure",
        source_id="source",
        width=100,
        height=100,
        elements=[
            {"element_id": "parent", "base_type": "container", "bbox": [0, 0, 100, 100]},
            {
                "element_id": "label",
                "base_type": "text",
                "parent_id": "parent",
                "bbox": [2, 2, 20, 20],
                "text_region_ids": ["ocr"],
            },
        ],
        texts=[
            {
                "text_region_id": "ocr",
                "effective_text": "原文",
                "bbox": [2, 2, 20, 20],
                "origin": "ocr",
                "geometry_origin": "ocr",
                "ocr_text_id": "ocr",
                "original_text": "原文",
                "original_score": 0.5,
            }
        ],
    )
    changed, _ = apply_actions(
        doc,
        ReviewPatch(
            request_id="closure-edit",
            actions=[
                {"action": "update_box", "element_id": "label", "bbox": [3, 3, 22, 22]},
                {"action": "delete_region", "element_id": "parent", "children": "detach_children"},
            ],
        ),
    )
    assert changed.texts[0].bbox == [3, 3, 22, 22]
    assert changed.texts[0].geometry_origin == "human" and changed.texts[0].origin == "ocr"
    assert changed.texts[0].original_score == 0.5 and changed.texts[0].original_text == "原文"
    assert changed.elements[0].parent_id is None
    assert doc.texts[0].bbox == [2, 2, 20, 20]
