"""Read-only acceptance of the three actual local browser review sessions."""

import hashlib
import json
import os
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image


@pytest.mark.ui_live
def test_review_browser_acceptance_preserves_original_evidence():
    root = Path(os.environ.get("LETSAIGC_UI_SEARCH_EVIDENCE", ".local/validation/ui-analysis"))
    evidence = root / "review-acceptance.json"
    if not evidence.is_file():
        pytest.skip("Actual local browser review evidence is not present on this machine")
    value = json.loads(evidence.read_text())
    assert value["status"] == "passed" and value["evidence_kind"] == "runtime"
    assert len(value["cases"]) == 3 and all(value["browser_checks"].values())
    assert value["external_calls"] == {"search": 0, "ocr": 0, "vlm": 0, "gpu": 0}
    assert value["historic_rows_unchanged"] and value["unknown_unsettled_usd"] == 0.25
    assert value["recovery"]["old_head_preserved"] and value["recovery"]["resumed_same_request"]
    for case in value["cases"]:
        assert case["pixels_exact"] and case["raw_ocr_preserved"]
        assert case["confirmed_revision"] == case["draft_revision"]
        assert case["evaluation_lane"] == "human_assisted"
    for screenshot in value["screenshots"]:
        data = (root / screenshot).read_bytes()
        with Image.open(BytesIO(data)) as image:
            assert image.format in {"PNG", "JPEG"} and image.width >= 760 and image.height >= 600
            image.verify()
        assert hashlib.sha256(data).hexdigest() == value["screenshot_sha256"][screenshot]
    assert value["quality_status"] == "pending"
    assert value["local_model_child_integration"] == "pending_T029_T030"
