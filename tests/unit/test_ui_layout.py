from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from letsaigc.schemas.ui import UIResourceLimits
from letsaigc.ui_analysis.crops import create_crops
from letsaigc.ui_analysis.layout import build_layout
from letsaigc.ui_analysis.normalize import make_views, normalize


def fixture(ui_store):
    pixels = np.arange(64 * 48 * 4, dtype=np.uint8).reshape(48, 64, 4)
    output = BytesIO()
    Image.fromarray(pixels, "RGBA").save(output, format="PNG")
    source = ui_store.put("layout-task", "input", output.getvalue(), role="original", media_type="image/png")
    canonical, _ = normalize(ui_store, source, "normalize", UIResourceLimits())
    view = make_views(ui_store, canonical, "views", UIResourceLimits())[0]
    texts = {"texts": [{"text_id": "text-a", "text": "Start", "polygon": [[5, 6], [15, 6], [15, 12], [5, 12]]}]}
    elements = [
        {"id": "panel", "kind": "panel", "bbox": [0, 0, 60, 40], "parent_id": None, "evidence_ids": ["overview"]},
        {"id": "button", "kind": "button", "bbox": [2, 3, 30, 23], "parent_id": "panel", "evidence_ids": ["overview"]},
        {
            "id": "same-button",
            "kind": "button",
            "bbox": [2, 3, 30, 23],
            "parent_id": "panel",
            "evidence_ids": ["overview"],
        },
    ]
    analysis = {
        "elements": elements,
        "observations": [],
        "hypotheses": [],
        "text_links": [],
        "occlusions": [],
        "correction_suggestions": [],
        "revision_proposals": [],
    }
    return pixels, canonical, view, texts, analysis


def test_dedup_smallest_containing_control_stable_ids_and_exact_crops(ui_store):
    pixels, canonical, view, texts, analysis = fixture(ui_store)
    layout = build_layout(canonical, view, texts, analysis)
    assert len(layout["elements"]) == 2
    button = next(element for element in layout["elements"] if element["kind"] == "button")
    assert button["text_ids"] == ["text-a"]
    assert layout["candidate_mapping"]["button"] == layout["candidate_mapping"]["same-button"]
    changed = {**analysis, "elements": [dict(element) for element in analysis["elements"]]}
    changed["elements"][1]["bbox"] = [3, 3, 31, 23]
    changed["elements"] = changed["elements"][:2]
    revised = build_layout(canonical, view, texts, changed, previous=layout, revision=1)
    assert (
        next(element["element_id"] for element in revised["elements"] if element["kind"] == "button")
        == button["element_id"]
    )
    assets = create_crops(ui_store, canonical, layout, "crop")
    cropped = next(asset for asset in assets["crops"] if asset["element_id"] == button["element_id"])
    with Image.open(BytesIO(ui_store.read(cropped["ref"]))) as image:
        assert np.array_equal(np.asarray(image), pixels[3:23, 2:30])
    assert ui_store.read(assets["overlay_ref"])


def test_invalid_geometry_rejected_and_ambiguous_text_unassigned(ui_store):
    _, canonical, view, texts, analysis = fixture(ui_store)
    analysis["elements"][1]["bbox"] = [-1, 0, 20, 20]
    with pytest.raises(ValueError):
        build_layout(canonical, view, texts, analysis)
    _, canonical, view, texts, analysis = fixture(ui_store)
    analysis["elements"] = [
        {"id": "a", "kind": "panel", "bbox": [0, 0, 20, 20], "parent_id": None, "evidence_ids": ["overview"]},
        {"id": "b", "kind": "button", "bbox": [0, 0, 20, 20], "parent_id": None, "evidence_ids": ["overview"]},
    ]
    layout = build_layout(canonical, view, texts, analysis)
    assert layout["unassigned_text_ids"] == ["text-a"]
