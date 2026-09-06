"""T023 prompt, canonical mask, and recoverable asset contracts."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from letsaigc.assets.store import ArtifactStore
from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.pipeline import ArtifactRef, canonical_json
from letsaigc.vision.base import SAMJob
from letsaigc.vision.segmentation import _predict, segment_sam_job


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _job(tmp_path, *, layout=False, keep=False, points=None):
    store = ArtifactStore(tmp_path / "artifacts")
    task = "segment-task"
    image = Image.new("RGB", (5, 3), (20, 40, 60))
    canonical = store.put(task, "input", _png(image), role="canonical", media_type="image/png")
    layout_ref = None
    target = {"kind": "bbox", "xyxy": [1, 0, 4, 3]}
    if layout:
        layout = {
            "schema_version": 1,
            "source_id": "source-1",
            "canonical_sha256": canonical.sha256,
            "canonical_ref": canonical.model_dump(mode="json"),
            "width": 5,
            "height": 3,
            "elements": [
                {"element_id": "target", "bbox": [1.2, 0.2, 3.1, 2.9]},
                {"element_id": "keep", "bbox": [0, 0, 1, 3]},
            ],
        }
        layout_ref = store.put(task, "input", canonical_json(layout).encode(), role="layout")
        target = {"kind": "element", "element_id": "target"}
    selection = {
        "schema_version": 1,
        "sources": [
            {
                "source_id": "source-1",
                "original_sha256": canonical.sha256,
                "layout_ref": layout_ref.model_dump(mode="json") if layout_ref else None,
                "target_regions": [target, {"kind": "element", "element_id": "keep"}] if keep else [target],
                "keep_elements": ["keep"] if keep else [],
                "remove_elements": [],
            }
        ],
    }
    selection["sources"][0] = {key: value for key, value in selection["sources"][0].items() if value is not None}
    selection_ref = store.put(
        task,
        "input",
        canonical_json(selection).encode(),
        role="selection",
        source_ids=[canonical.artifact_id],
    )
    snapshot = store.put(task, "model", b"snapshot", role="model_snapshot")
    prompt_box = [0, 0, 1, 3] if keep else [1, 0, 4, 3]
    prompt_id = "keep" if keep else "target"
    prompt = {"element_id": prompt_id, "box": prompt_box, "points": points or []}
    job = SAMJob(
        task_id=task,
        operation_id="operation",
        canonical_ref=canonical,
        selection_ref=selection_ref,
        selection_revision=2,
        selection_hash=selection_ref.sha256,
        model_snapshot_ref=snapshot,
        model_digest=snapshot.sha256,
        prompts=[prompt],
        parameters_hash="a" * 64,
    )
    return store, job, image


class FakeProcessor:
    def __init__(self, masks):
        self.masks = masks

    def __call__(self, **kwargs):
        assert kwargs["return_tensors"] == "pt"
        return {"pixel_values": np.zeros((1, 3, 1024, 1024), dtype=np.float32), "original_sizes": [(3, 5)]}

    def post_process_masks(self, masks, *, original_sizes, mask_threshold, binarize):
        assert original_sizes == [(3, 5)]
        assert mask_threshold == 0.0 and binarize is False
        return [self.masks]


class CallableModel:
    def __init__(self, mask, score=0.9):
        self.mask = mask
        self.score = score

    def __call__(self, **_kwargs):
        return SimpleNamespace(pred_masks=self.mask, iou_scores=np.asarray([self.score]))


def _engine(mask, score=0.9):
    return SimpleNamespace(
        processor=FakeProcessor(mask),
        model=CallableModel(mask, score),
    )


def _read_image(store, ref):
    with Image.open(io.BytesIO(store.read(ArtifactRef.model_validate(ref)))) as image:
        return image.copy()


def test_odd_non_square_prompt_maps_full_canonical_and_preserves_original(tmp_path):
    store, job, image = _job(tmp_path, points=[(2.0, 1.0)])
    mask = np.zeros((3, 5), dtype=np.float32)
    mask[0:2, 1:4] = 0.9
    bundle = segment_sam_job(_engine(mask), store, job)
    assert bundle["status"] == "ready"
    item = bundle["items"][0]
    assert item["status"] == "ready"
    assert item["canonical_sha256"] == job.canonical_ref.sha256
    assert item["contour_mask_ref"]["role"] == "contour_mask"
    assert item["estimated_alpha_ref"]["role"] == "estimated_alpha"
    assert item["rect_crop_ref"]["role"] == "rect_crop"
    assert item["visible_crop_ref"]["role"] == "visible_crop"
    assert item["rect_crop_ref"]["source_ids"] == [job.canonical_ref.artifact_id, job.selection_ref.artifact_id]
    assert _read_image(store, job.canonical_ref).tobytes() == image.tobytes()
    assert _read_image(store, item["contour_mask_ref"]).size == image.size
    assert _read_image(store, item["estimated_alpha_ref"]).size == image.size
    assert _read_image(store, item["rect_crop_ref"]).size == (3, 3)
    visible = _read_image(store, item["visible_crop_ref"])
    assert visible.mode == "RGBA" and 0 < visible.getpixel((0, 0))[3] <= 255


def test_layout_float_boxes_use_shared_floor_ceil_mapping(tmp_path):
    store, job, _ = _job(tmp_path, layout=True)
    mask = np.ones((3, 5), dtype=np.float32)
    bundle = segment_sam_job(_engine(mask), store, job)
    assert bundle["items"][0]["bbox"] == [1, 0, 4, 3]


def test_keep_element_and_prompt_point_outside_are_rejected(tmp_path):
    store, job, _ = _job(tmp_path, layout=True, keep=True)
    with pytest.raises(PipelineError) as raised:
        segment_sam_job(_engine(np.ones((3, 5), dtype=np.float32)), store, job)
    assert raised.value.code == "input_changed"

    store, job, _ = _job(tmp_path / "point", points=[(4.5, 1.0)])
    with pytest.raises(PipelineError) as raised:
        segment_sam_job(_engine(np.ones((3, 5), dtype=np.float32)), store, job)
    assert raised.value.code == "input_changed"


def test_empty_mask_keeps_all_refs_and_is_unknown_not_ready(tmp_path):
    store, job, image = _job(tmp_path)
    bundle = segment_sam_job(_engine(np.zeros((3, 5), dtype=np.float32)), store, job)
    assert bundle["status"] == "unknown"
    item = bundle["items"][0]
    assert item["status"] == "unknown"
    assert item["confidence"] is None
    assert item["reason"] == "sam_mask_empty"
    assert _read_image(store, item["contour_mask_ref"]).getbbox() is None
    assert _read_image(store, item["estimated_alpha_ref"]).size == image.size
    assert _read_image(store, item["visible_crop_ref"]).getchannel("A").getbbox() is None


def test_model_failure_retains_completed_unknown_asset(tmp_path):
    store, job, _ = _job(tmp_path)

    class FailingProcessor(FakeProcessor):
        def __call__(self, **_kwargs):
            raise RuntimeError("provider details must not escape")

    engine = SimpleNamespace(
        processor=FailingProcessor(np.zeros((3, 5), dtype=np.float32)),
        model=CallableModel(np.zeros((3, 5), dtype=np.float32)),
    )
    bundle = segment_sam_job(engine, store, job)
    assert bundle["status"] == "unknown"
    assert bundle["items"][0]["status"] == "unknown"
    assert bundle["items"][0]["reason"] == "segmentation_failed"


def test_mask_unpack_rejects_an_unexpected_multimask_axis():
    processor = FakeProcessor(np.zeros((2, 3, 5), dtype=np.float32))
    engine = SimpleNamespace(
        processor=processor,
        model=CallableModel(np.zeros((1, 2, 3, 5), dtype=np.float32)),
    )
    prompt = SimpleNamespace(box=(0, 0, 5, 3), points=[])
    with pytest.raises(PipelineError) as raised:
        _predict(engine, Image.new("RGB", (5, 3)), prompt, (3, 5))
    assert raised.value.code == "segmentation_failed"


def test_partial_bundle_keeps_first_refs_when_second_prompt_fails(tmp_path):
    store, job, _ = _job(tmp_path)
    payload = json.loads(store.read(job.selection_ref))
    payload["sources"][0]["target_regions"] = [
        {"kind": "bbox", "xyxy": [0, 0, 2, 3]},
        {"kind": "bbox", "xyxy": [3, 0, 5, 3]},
    ]
    selection_ref = store.put(
        job.task_id,
        "input",
        canonical_json(payload).encode(),
        role="selection",
        source_ids=[job.canonical_ref.artifact_id],
    )
    job = SAMJob.model_validate(
        job.model_dump(mode="json")
        | {
            "selection_ref": selection_ref.model_dump(mode="json"),
            "selection_hash": selection_ref.sha256,
            "prompts": [
                {"element_id": "left", "box": [0, 0, 2, 3], "points": []},
                {"element_id": "right", "box": [3, 0, 5, 3], "points": []},
            ],
        }
    )
    mask = np.zeros((3, 5), dtype=np.float32)
    mask[:, :2] = 2.0

    class FailsSecond:
        def __init__(self):
            self.calls = 0

        def __call__(self, **_kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("provider failure")
            return SimpleNamespace(pred_masks=mask, iou_scores=np.asarray([0.9]))

    engine = SimpleNamespace(processor=FakeProcessor(mask), model=FailsSecond())
    bundle = segment_sam_job(engine, store, job)
    assert bundle["status"] == "partial"
    assert bundle["items"][0]["status"] == "ready"
    assert bundle["items"][1]["status"] == "unknown"
    assert store.read(ArtifactRef.model_validate(bundle["items"][0]["contour_mask_ref"]))
    assert bundle["items"][1]["reason"] == "segmentation_failed"
