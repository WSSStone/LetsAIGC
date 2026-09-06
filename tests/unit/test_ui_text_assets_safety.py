"""Independent checks that glyph evidence remains image-derived and scoped."""

from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageDraw

from letsaigc.assets.store import ArtifactStore
from letsaigc.pipelines.errors import PipelineError
from letsaigc.ui_analysis.text_assets import extract_glyph_assets


def setup(tmp_path, *, glyph=False, foreign_mask=False):
    store = ArtifactStore(tmp_path / "artifacts")

    def put(image, role, sources=()):
        stream = BytesIO()
        image.save(stream, format="PNG")
        return store.put(
            "task", "input", stream.getvalue(), role=role, media_type="image/png", source_ids=list(sources),
        )

    image = Image.new("RGB", (32, 24), "white")
    if glyph:
        draw = ImageDraw.Draw(image)
        draw.line((8, 5, 8, 18), fill="black", width=3)
        draw.line((18, 5, 18, 18), fill="black", width=3)
        draw.line((8, 12, 18, 12), fill="black", width=3)
    canonical = put(image, "canonical")
    contour = Image.new("L", image.size, 0)
    ImageDraw.Draw(contour).rectangle((6, 3, 21, 20), fill=255)
    mask = put(contour, "contour_mask", ["wrong-canonical" if foreign_mask else canonical.artifact_id])
    return store, image, canonical, mask


def test_uniform_pixels_plus_a_control_contour_cannot_become_a_glyph(tmp_path):
    store, _, canonical, mask = setup(tmp_path)
    result = extract_glyph_assets(store, canonical, {"text_id": "text", "bbox": [2, 2, 29, 22]},
                                  operation_id="extract", segmentation_mask_ref=mask)
    assert result["status"] == "unavailable" and result["glyph_image_ref"] is None


def test_same_task_contour_from_another_canonical_is_rejected(tmp_path):
    store, _, canonical, mask = setup(tmp_path, glyph=True, foreign_mask=True)
    with pytest.raises(PipelineError):
        extract_glyph_assets(store, canonical, {"text_id": "text", "bbox": [2, 2, 29, 22]},
                             operation_id="extract", segmentation_mask_ref=mask)


def test_fractional_ocr_extent_uses_enclosing_pixels_and_rejects_raw_overflow(tmp_path):
    store, _, canonical, _ = setup(tmp_path)
    result = extract_glyph_assets(store, canonical, {"text_id": "text", "bbox": [2.2, 2.1, 28.2, 20.6]},
                                  operation_id="extract")
    assert result["bbox"] == [2, 2, 29, 21]
    with pytest.raises(PipelineError):
        extract_glyph_assets(store, canonical, {"text_id": "text", "bbox": [0, 0, 32.2, 24]},
                             operation_id="overflow")


@pytest.mark.parametrize("with_contour", [False, True])
def test_binary_glyph_does_not_extract_white_background_edges(tmp_path, with_contour):
    store, image, canonical, mask = setup(tmp_path, glyph=True)
    result = extract_glyph_assets(store, canonical, {"text_id": "text", "bbox": [2, 2, 29, 22]},
                                  operation_id="extract", segmentation_mask_ref=mask if with_contour else None)
    assert result["glyph_mask_ref"] is not None
    with Image.open(BytesIO(store.read(result["glyph_mask_ref"]))) as loaded:
        foreground = np.asarray(loaded) > 0
    pixels = np.asarray(image.crop((2, 2, 29, 22)))
    assert foreground.any()
    assert np.all(pixels[foreground] == 0)
