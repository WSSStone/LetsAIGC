"""Independent source and candidate integrity checks for T021."""

import json
from io import BytesIO

import pytest
from PIL import Image

from letsaigc.assets.store import ArtifactStore
from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.pipeline import canonical_json
from letsaigc.ui_analysis.selection import (
    TrustedSource,
    create_candidate,
    propose_candidates,
    select_candidate,
    selection_catalog,
    validate_selection,
)


def source_fixture(tmp_path, *, foreign_canonical=False):
    store = ArtifactStore(tmp_path / "artifacts")

    def picture(color):
        stream = BytesIO()
        Image.new("RGB", (20, 20), color).save(stream, format="PNG")
        return stream.getvalue()

    original = store.put("root", "input", picture("red"), role="original", media_type="image/png")
    canonical = store.put(
        "root", "normalize", picture("blue" if foreign_canonical else "red"),
        role="canonical", media_type="image/png", source_ids=[original.artifact_id],
    )
    layout = {
        "schema_version": 1, "source_id": "source-1", "width": 20, "height": 20,
        "canonical_ref": canonical.model_dump(mode="json"), "canonical_sha256": canonical.sha256,
        "elements": [{"element_id": "whole", "bbox": [0, 0, 20, 20]}],
    }
    ref = store.put("root", "layout", canonical_json(layout).encode(), role="layout")
    source = TrustedSource("source-1", original, width=20, height=20, layout_ref=ref)
    value = {
        "schema_version": 1,
        "sources": [{
            "source_id": "source-1", "original_sha256": original.sha256,
            "layout_ref": ref.model_dump(mode="json"),
            "target_regions": [{"kind": "element", "element_id": "whole"}],
            "keep_elements": [], "remove_elements": [],
        }],
    }
    return store, source, value


def test_same_dimensions_and_claimed_source_do_not_bind_an_unrelated_canonical(tmp_path):
    store, source, value = source_fixture(tmp_path, foreign_canonical=True)
    with pytest.raises(PipelineError):
        validate_selection(store, "root", value, {source.source_id: source})


def test_recorded_candidate_rechecks_original_bytes_before_selection(tmp_path):
    store, source, value = source_fixture(tmp_path)
    candidate = create_candidate(store, "root", value, {source.source_id: source})
    store.resolve(source.original_ref).write_bytes(b"changed-after-preview")
    with pytest.raises(PipelineError):
        select_candidate(store, "root", candidate.candidate.candidate_id)


def test_removal_may_span_two_adjacent_target_regions_without_expanding_them(tmp_path):
    store, source, value = source_fixture(tmp_path)
    value["sources"][0].update(
        target_regions=[
            {"kind": "bbox", "xyxy": [0, 0, 10, 20]},
            {"kind": "bbox", "xyxy": [10, 0, 20, 20]},
        ],
        remove_elements=["whole"],
    )
    parsed = validate_selection(store, "root", value, {source.source_id: source})
    assert json.loads(parsed.model_dump_json())["sources"][0]["target_regions"] == value["sources"][0]["target_regions"]


def test_user_can_replace_one_candidate_with_another_without_losing_options(tmp_path):
    store, source, value = source_fixture(tmp_path)
    other = json.loads(json.dumps(value))
    other["sources"][0]["target_regions"] = [{"kind": "bbox", "xyxy": [1, 1, 10, 10]}]
    propose_candidates(store, "root", [value, other], {source.source_id: source})
    first = select_candidate(store, "root", "candidate-1")
    second = select_candidate(store, "root", "candidate-2")
    assert first.selection_ref.sha256 != second.selection_ref.sha256
    assert selection_catalog(store, "root").revision == 2
    assert len(selection_catalog(store, "root").candidates) == 2
    assert select_candidate(store, "root", "candidate-2").selection_ref == second.selection_ref
    assert selection_catalog(store, "root").revision == 2


def test_bbox_preview_uses_exif_corrected_canonical_coordinates(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    stream = BytesIO()
    picture = Image.new("RGB", (20, 10), "red")
    exif = Image.Exif()
    exif[274] = 6
    picture.save(stream, format="JPEG", exif=exif)
    ref = store.put("root", "input", stream.getvalue(), role="original", media_type="image/jpeg")
    source = TrustedSource("source-1", ref)
    value = {"sources": [{"source_id": "source-1", "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 10, 20]}]}]}
    candidate = create_candidate(store, "root", value, {source.source_id: source})
    with Image.open(BytesIO(store.read(candidate.candidate.preview_ref))) as preview:
        assert preview.size == (10, 20)
