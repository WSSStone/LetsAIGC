import json

import pytest
from PIL import Image

from letsaigc.assets.store import ArtifactStore
from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.pipeline import canonical_json
from letsaigc.ui_analysis.selection import (
    TrustedSource,
    create_candidate,
    find_candidate,
    select_candidate,
    validate_selection,
)


def fixture(tmp_path):
    from io import BytesIO

    store = ArtifactStore(tmp_path)
    stream = BytesIO()
    Image.new("RGB", (20, 20), "white").save(stream, format="PNG")
    original = store.put("task", "input", stream.getvalue(), role="original", media_type="image/png")
    canonical = store.put("task", "normalize", stream.getvalue(), role="canonical", media_type="image/png")
    layout = {
        "schema_version": 1,
        "source_id": "source-1",
        "canonical_ref": canonical.model_dump(mode="json"),
        "canonical_sha256": canonical.sha256,
        "width": 20,
        "height": 20,
        "elements": [
            {"element_id": "left", "bbox": [0, 0, 10, 20]},
            {"element_id": "right", "bbox": [10, 0, 20, 20]},
        ],
    }
    layout_ref = store.put("task", "layout", canonical_json(layout).encode(), role="layout")
    source = TrustedSource("source-1", original, width=20, height=20, layout_ref=layout_ref)
    return store, source


def selection(**overrides):
    source = {
        "source_id": "source-1",
        "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 20, 20]}],
        "keep_elements": [],
        "remove_elements": [],
    }
    source.update(overrides)
    return {"schema_version": 1, "sources": [source]}


def test_selection_fills_trusted_hash_and_rejects_unknown_fields(tmp_path):
    store, source = fixture(tmp_path)
    parsed = validate_selection(store, "task", selection(), {source.source_id: source})
    assert parsed.sources[0].original_sha256 == source.original_sha256
    with pytest.raises(PipelineError) as caught:
        validate_selection(store, "task", {**selection(), "extra": True}, {source.source_id: source})
    assert caught.value.code == "invalid_selection"


def test_selection_allows_remove_box_covered_by_region_union(tmp_path):
    store, source = fixture(tmp_path)
    value = selection(
        target_regions=[
            {"kind": "bbox", "xyxy": [0, 0, 10, 20]},
            {"kind": "bbox", "xyxy": [10, 0, 20, 20]},
        ],
        layout_ref=source.layout_ref.model_dump(mode="json"),
        remove_elements=["left", "right"],
    )
    parsed = validate_selection(store, "task", value, {source.source_id: source})
    assert parsed.sources[0].remove_elements == ["left", "right"]


def test_candidate_selection_is_local_and_zero_model_calls(tmp_path):
    store, source = fixture(tmp_path)
    value = selection(
        layout_ref=source.layout_ref.model_dump(mode="json"),
        keep_elements=["left"],
    )
    prepared = create_candidate(store, "task", value, {source.source_id: source})
    assert prepared.model_calls == 0
    assert prepared.candidate.candidate_id == "candidate-1"
    chosen = select_candidate(store, "task", "candidate-1")
    assert chosen.model_calls == 0
    assert json.loads(store.read(chosen.selection_ref)) == json.loads(store.read(prepared.candidate.selection_ref))
    assert find_candidate(store, "task", "candidate-1").selection_ref.sha256 == prepared.candidate.selection_ref.sha256


def test_selection_rejects_out_of_bounds_bbox(tmp_path):
    store, source = fixture(tmp_path)
    with pytest.raises(PipelineError, match="outside"):
        validate_selection(
            store,
            "task",
            selection(target_regions=[{"kind": "bbox", "xyxy": [0, 0, 21, 20]}]),
            {source.source_id: source},
        )
