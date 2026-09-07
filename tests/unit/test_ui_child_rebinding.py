"""Lossless child rebinding cannot change human geometry or referenced pixels."""

import json

import pytest

from letsaigc.assets.store import ArtifactStore
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.ui_children import validate_rebound_selection
from letsaigc.schemas.pipeline import canonical_json


@pytest.mark.parametrize("mutation", [None, "box", "lock", "pixels", "foreign_scope"])
def test_child_rebinding_accepts_only_scope_changes(tmp_path, mutation):
    store = ArtifactStore(tmp_path)

    def selection(task, pixels, box, locked):
        canonical = store.put(task, "input", pixels, role="canonical", media_type="image/png")
        layout = store.put(task, "layout", canonical_json({
            "task_id": task, "canonical_ref": canonical.model_dump(mode="json"),
            "elements": [{"element_id": "label", "bbox": box, "locked": locked}],
        }).encode(), role="review_layout")
        return store.put(task, "selection", canonical_json({
            "schema_version": 1, "sources": [{"source_id": "source-1", "original_sha256": "a" * 64,
                "layout_ref": layout.model_dump(mode="json"),
                "target_regions": [{"kind": "element", "element_id": "label"}]}],
        }).encode(), role="selection")

    root = selection("root", b"frozen-pixels", [1, 2, 6, 9], True)
    child = selection("child", b"different-pixels" if mutation == "pixels" else b"frozen-pixels",
                      [1, 2, 7, 9] if mutation == "box" else [1, 2, 6, 9], mutation != "lock")
    if mutation == "foreign_scope":
        payload = json.loads(store.read(child))
        other = selection("other", b"frozen-pixels", [1, 2, 6, 9], True)
        payload["sources"][0]["layout_ref"] = json.loads(store.read(other))["sources"][0]["layout_ref"]
        child = store.put("child", "selection", canonical_json(payload).encode(), role="selection")
    if mutation is None:
        validate_rebound_selection(store, root, child, task_id="child")
    else:
        with pytest.raises(PipelineError):
            validate_rebound_selection(store, root, child, task_id="child")
