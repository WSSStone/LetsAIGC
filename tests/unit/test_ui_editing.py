"""Offline T028 editing planner tests.

The planner tests intentionally use synthetic artifacts and never configure a
SAM/Comfy backend.  Their purpose is to prove deterministic registration,
selection gating, exact child bindings, and CPU mask semantics.
"""

from io import BytesIO

import pytest
from PIL import Image

from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef, canonical_json
from letsaigc.schemas.ui import UIAnalysisRequest
from letsaigc.ui_analysis.editing import EditingExecution, reconstruct_edit_mask

BUDGET = {
    "max_total_cost_usd": 1.0,
    "max_iteration_cost_usd": 0.5,
    "max_total_gpu_minutes": 10.0,
    "max_iteration_gpu_minutes": 5.0,
    "max_revisions": 2,
}


def _png(mode="RGB", size=(8, 6), color="white"):
    stream = BytesIO()
    Image.new(mode, size, color).save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def editing_family(tmp_path):
    service = PipelineService(tmp_path, ui_schema=5)
    original = service.artifacts.put("root", "input", _png(), role="original", media_type="image/png")
    selection = service.artifacts.put(
        "root",
        "selection-input",
        canonical_json(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "source_id": original.artifact_id,
                        "original_sha256": original.sha256,
                        "target_regions": [{"kind": "bbox", "xyxy": [1, 1, 5, 5]}],
                        "keep_elements": [],
                        "remove_elements": [],
                    }
                ],
            }
        ).encode(),
        role="selection",
    )
    request = UIAnalysisRequest(
        input={"kind": "manual", "inputs": [original]},
        output_mode="decompose",
        selection_mode="bound",
        selection_ref=selection,
        budget=BUDGET,
    )
    root = service.ui_plan("root", request)
    canonical = service.artifacts.put("root", "normalize", _png(), role="canonical", media_type="image/png")
    snapshot = service.artifacts.put("root", "plan", b'{"fixture":true}', role="model_snapshot")
    return service, root, selection, canonical, snapshot, original


def test_prepare_is_offline_deterministic_and_binding_is_frozen(editing_family):
    service, root, selection, canonical, snapshot, original = editing_family
    planner = EditingExecution(service)

    first = planner.prepare(root, canonical_ref=canonical, model_snapshot_ref=snapshot)
    second = planner.prepare(root, canonical_ref=canonical, model_snapshot_ref=snapshot)

    assert first.state == "awaiting_approval"
    assert first.child is not None
    assert first.child == second.child
    assert first.child.workflow_type == "ui_segmentation"
    binding = planner.binding(first.child)
    assert binding.capability == "ui.segment"
    assert binding.inputs == first.child.inputs
    assert binding.selection_hash == binding.selection_ref.sha256
    assert planner.validate_current(first.child)


def test_prepare_waits_for_selection_without_child(editing_family):
    service, root, *_ = editing_family
    original = service.artifacts.put("deferred", "input", _png(), role="original", media_type="image/png")
    request = UIAnalysisRequest(
        input={"kind": "manual", "inputs": [original]},
        output_mode="decompose",
        selection_mode="deferred",
        budget=BUDGET,
    )
    root = service.ui_plan("deferred", request)
    result = EditingExecution(service).prepare(root, canonical_ref=root.inputs[0])
    assert result.state == "awaiting_selection"
    assert result.child is None


def test_current_selection_change_rejects_old_child(editing_family):
    service, root, selection, canonical, snapshot, original = editing_family
    planner = EditingExecution(service)
    child = planner.prepare(root, canonical_ref=canonical, model_snapshot_ref=snapshot).child
    assert child is not None
    newer = service.artifacts.put(
        "root",
        "selection-new",
        service.artifacts.read(selection).replace(b"1,1,5,5", b"0,0,5,5"),
        role="selection",
    )
    # Record the newer selection in the immutable catalog.  This is the same
    # provider-free operation used by the CLI selection path.
    from letsaigc.ui_analysis.selection import record_selection

    record_selection(service.artifacts, root.task_id, newer, revision=1)
    with pytest.raises(PipelineError) as caught:
        planner.validate_current(child)
    assert caught.value.code == "selection_superseded"


def test_empty_and_keep_constrained_masks_are_explicit():
    image = {
        "schema_version": 1,
        "sources": [
            {
                "source_id": "source-1",
                "original_sha256": "a" * 64,
                "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 4, 4]}],
                "keep_elements": [],
                "remove_elements": [],
            }
        ],
    }
    result = reconstruct_edit_mask(image, "source-1", size=(8, 8))
    assert result.status == "ready"
    assert result.mask.getbbox() == (0, 0, 4, 4)

    layout_ref = ArtifactRef(
        task_id="root",
        artifact_id="layout",
        key="root/layout/layout",
        sha256="b" * 64,
        size_bytes=0,
        media_type="application/json",
        role="layout",
        operation_id="layout",
    )
    empty = {
        **image,
        "sources": [
            {
                **image["sources"][0],
                "layout_ref": layout_ref.model_dump(mode="json"),
                "target_regions": [{"kind": "element", "element_id": "element-1"}],
            }
        ],
    }
    empty_result = reconstruct_edit_mask(
        empty,
        "source-1",
        size=(8, 8),
        segmentation_masks={"element-1": Image.new("L", (8, 8), 0)},
    )
    assert empty_result.status == "no_edit_pixels"
