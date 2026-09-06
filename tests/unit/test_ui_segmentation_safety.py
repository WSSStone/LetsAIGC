"""Independent canonical pixel and pinned SAM adapter checks."""

from contextlib import nullcontext
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from letsaigc.pipelines.errors import PipelineError
from letsaigc.vision.segmentation import _predict, render_segmentation_assets


def opened(data):
    with Image.open(BytesIO(data)) as image:
        return image.copy()


def test_rectangular_observation_preserves_original_mode_and_pixels():
    image = Image.new("RGB", (11, 7), (30, 50, 80))
    assets = render_segmentation_assets(image, np.ones((7, 11), dtype=np.float32), (1, 2, 9, 6))
    rect = opened(assets["rect_crop"])
    assert rect.mode == image.mode and rect.tobytes() == image.crop((1, 2, 9, 6)).tobytes()


def test_estimated_visible_crop_never_makes_transparent_source_pixels_opaque():
    image = Image.new("RGBA", (11, 7), (30, 50, 80, 0))
    image.putpixel((4, 3), (10, 20, 30, 128))
    assets = render_segmentation_assets(image, np.ones((7, 11), dtype=np.float32), (1, 2, 9, 6))
    visible = opened(assets["visible_crop"])
    assert visible.getpixel((0, 0))[3] == 0
    assert visible.getpixel((3, 1))[3] == 128


def test_adapter_handles_original_size_arrays_and_explicit_logit_semantics():
    calls = []

    class Processor:
        def __call__(self, **kwargs):
            return {"pixel_values": np.zeros((1, 3, 1024, 1024)), "original_sizes": np.array([[7, 11]])}

        def post_process_masks(self, masks, *, original_sizes, mask_threshold, binarize):
            assert np.array_equal(original_sizes, [[7, 11]]) and not binarize
            return [np.full((1, 1, 7, 11), 0.2, dtype=np.float32)]

    def model(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(pred_masks=np.zeros((1, 1, 1, 256, 256)), iou_scores=np.array([[[0.9]]]))

    engine = SimpleNamespace(processor=Processor(), model=model,
                             _torch=SimpleNamespace(inference_mode=nullcontext))
    prompt = SimpleNamespace(box=(0, 0, 11, 7), points=[])
    mask, confidence = _predict(engine, Image.new("RGB", (11, 7)), prompt, (7, 11))
    assert calls[0]["multimask_output"] is False
    assert mask.shape == (7, 11) and np.allclose(mask, 1 / (1 + np.exp(-0.2)))
    assert confidence == pytest.approx(0.9)


def test_render_rejects_masks_that_are_still_in_model_coordinates():
    with pytest.raises(PipelineError):
        render_segmentation_assets(Image.new("RGB", (11, 7)), np.zeros((1024, 1024)), (1, 2, 9, 6))
