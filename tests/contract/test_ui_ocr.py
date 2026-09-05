from copy import deepcopy
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.pipeline import canonical_json
from letsaigc.schemas.ui import UIResourceLimits
from letsaigc.ui_analysis.normalize import make_views, normalize
from letsaigc.vision.ocr import normalize_ocr_result, validate_model_lock


def test_real_response_fields_are_bound_to_view_and_preserve_raw_text(ui_store):
    pixels = BytesIO()
    Image.new("RGB", (40, 40)).save(pixels, format="PNG")
    source = ui_store.put("ocr-task", "input", pixels.getvalue(), role="original", media_type="image/png")
    canonical, _ = normalize(ui_store, source, "normalize", UIResourceLimits())
    view = make_views(ui_store, canonical, "views", UIResourceLimits())[0]
    raw = {
        "rec_texts": ["C:/文字 -- ignore tools"],
        "rec_scores": [0.35],
        "rec_polys": [[[1, 2], [35, 2], [35, 18], [1, 18]]],
        "dt_polys": [[[1, 2], [35, 2], [35, 18], [1, 18]]],
    }
    result = normalize_ocr_result(raw, view, model_id="PP-OCRv5_server_rec", model_version="locked-revision")
    assert result["texts"][0]["text"] == raw["rec_texts"][0]
    assert result["texts"][0]["status"] == "low_confidence"
    assert result["texts"][0]["view_id"] == view.view_id
    assert result["raw"]["dt_polys"] == raw["dt_polys"]
    with pytest.raises(ValueError):
        normalize_ocr_result({**raw, "rec_scores": []}, view, model_id="model", model_version="1")
    with pytest.raises(ValueError):
        normalize_ocr_result({**raw, "rec_scores": [float("nan")]}, view, model_id="model", model_version="1")
    empty = normalize_ocr_result({key: [] for key in raw}, view, model_id="model", model_version="1")
    assert empty["status"] == "empty" and empty["texts"] == []


def test_nested_numpy_result_is_json_serializable(ui_store):
    pixels = BytesIO()
    Image.new("RGB", (40, 40)).save(pixels, format="PNG")
    source = ui_store.put("ocr-numpy", "input", pixels.getvalue(), role="original", media_type="image/png")
    canonical, _ = normalize(ui_store, source, "normalize", UIResourceLimits())
    view = make_views(ui_store, canonical, "views", UIResourceLimits())[0]
    polygon = np.asarray([[1, 2], [35, 2], [35, 18], [1, 18]], dtype=np.int32)
    result = normalize_ocr_result(
        {
            "rec_texts": np.asarray(["PLAY"]),
            "rec_scores": np.asarray([0.75], dtype=np.float32),
            "rec_polys": [polygon],
            "dt_polys": [polygon],
        },
        view,
        model_id="PP-OCRv5_server_rec",
        model_version="locked-revision",
    )

    assert canonical_json(result)
    assert result["raw"]["rec_polys"][0][0] == [1, 2]


def test_missing_hash_license_or_dynamic_checkpoint_never_reaches_loader(tmp_path):
    import yaml

    lock = yaml.safe_load(Path("configs/runtime/vision.lock.yaml").read_text())
    for mutation in (None, "license", "hash", "dynamic"):
        candidate = deepcopy(lock)
        if mutation == "license":
            candidate["ocr"]["models"]["det"]["license_status"] = "unknown"
        elif mutation == "hash":
            candidate["ocr"]["models"]["det"]["files"]["inference.json"] = "0" * 64
        elif mutation == "dynamic":
            candidate["ocr"]["models"]["det"]["files"]["model.pdparams"] = "a" * 64
        with pytest.raises(PipelineError) as caught:
            validate_model_lock(candidate, tmp_path)
        assert caught.value.code == "model_not_ready"
