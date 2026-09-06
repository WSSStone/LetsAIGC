"""CPU-only glyph extraction keeps uncertainty and source provenance explicit."""

from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageDraw

from letsaigc.assets.store import ArtifactStore
from letsaigc.pipelines.errors import PipelineError
from letsaigc.ui_analysis.text_assets import extract_glyph_assets


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def store(tmp_path):
    return ArtifactStore(tmp_path / "artifacts")


def _canonical(store, image: Image.Image):
    return store.put("text-task", "canonical", _png(image), role="canonical", media_type="image/png")


def test_english_and_geometric_chinese_region_yields_estimated_transparent_glyph(store):
    image = Image.new("RGB", (96, 48), "white")
    drawing = ImageDraw.Draw(image)
    drawing.text((8, 7), "AB", fill="black")
    # A few connected strokes approximate a Chinese glyph without relying on
    # a platform-specific CJK font.
    drawing.line((52, 8, 52, 28), fill="black", width=2)
    drawing.line((42, 18, 64, 18), fill="black", width=2)
    drawing.rectangle((45, 11, 61, 25), outline="black", width=1)
    canonical = _canonical(store, image)

    result = extract_glyph_assets(
        store,
        canonical,
        {"text_id": "text-ab-zh", "bbox": [4, 4, 70, 34], "score": 0.98},
        operation_id="glyph-1",
    )

    assert result["status"] == "estimated"
    assert result["rect_crop_ref"].role == "rect_crop"
    assert result["glyph_mask_ref"].role == "glyph_mask"
    assert result["glyph_image_ref"].role == "glyph_image"
    assert result["provenance_ref"].role == "provenance"
    mask = Image.open(BytesIO(store.read(result["glyph_mask_ref"])))
    glyph = Image.open(BytesIO(store.read(result["glyph_image_ref"])))
    assert mask.size == glyph.size == (66, 30)
    assert 0 < np.count_nonzero(np.asarray(mask)) < mask.width * mask.height
    assert glyph.mode == "RGBA"
    assert np.array_equal(np.asarray(mask), np.asarray(glyph)[:, :, 3])
    provenance = __import__("json").loads(store.read(result["provenance_ref"]))
    assert provenance["bbox"] == [4, 4, 70, 34]
    assert provenance["canonical_ref"]["sha256"] == canonical.sha256


def test_contour_mask_is_full_size_and_refines_glyph_to_text_roi(store):
    image = Image.new("RGB", (40, 28), "white")
    ImageDraw.Draw(image).rectangle((8, 8, 15, 18), fill="black")
    canonical = _canonical(store, image)
    contour = Image.new("L", image.size, 0)
    ImageDraw.Draw(contour).rectangle((8, 8, 15, 18), fill=255)
    contour_ref = store.put(
        "text-task",
        "sam",
        _png(contour),
        role="contour_mask",
        media_type="image/png",
        source_ids=[canonical.artifact_id],
    )

    result = extract_glyph_assets(
        store,
        canonical,
        {"text_id": "text-1", "bbox": [4, 4, 22, 23]},
        operation_id="glyph-contour",
        segmentation_mask_ref=contour_ref,
    )

    assert result["status"] == "estimated"
    assert result["glyph_mask_ref"].source_ids == [canonical.artifact_id, contour_ref.artifact_id]
    assert np.count_nonzero(np.asarray(Image.open(BytesIO(store.read(result["glyph_mask_ref"])))) > 0) > 0


def test_ocr_polygon_without_bbox_uses_enclosing_canonical_pixels(store):
    image = Image.new("RGB", (32, 24), "white")
    ImageDraw.Draw(image).rectangle((8, 6, 16, 15), fill="black")
    canonical = _canonical(store, image)
    result = extract_glyph_assets(
        store,
        canonical,
        {"text_id": "polygon-only", "polygon": [[8.2, 6.1], [16.1, 6.1], [16.1, 15.2], [8.2, 15.2]]},
        operation_id="glyph-polygon",
    )
    assert result["bbox"] == [8, 6, 17, 16]
    assert result["rect_crop_ref"].size_bytes > 0


def test_blank_uniform_and_low_contrast_regions_are_unavailable(store):
    blank = _canonical(store, Image.new("RGB", (32, 24), (128, 128, 128)))
    result = extract_glyph_assets(store, blank, {"text_id": "blank", "bbox": [2, 2, 28, 20]}, operation_id="blank")
    assert result["status"] == "unavailable"
    assert result["glyph_mask_ref"] is None and result["glyph_image_ref"] is None
    assert result["rect_crop_ref"].role == "rect_crop"

    low = Image.new("RGB", (32, 24), (128, 128, 128))
    ImageDraw.Draw(low).text((8, 5), "A", fill=(135, 135, 135))
    low_ref = _canonical(store, low)
    low_result = extract_glyph_assets(
        store, low_ref, {"text_id": "low", "bbox": [2, 2, 28, 20]}, operation_id="low"
    )
    assert low_result["status"] == "unavailable"


def test_uniform_rgb_background_supports_simple_colour_and_rejects_complex_colour(store):
    colour = Image.new("RGB", (32, 24), "white")
    ImageDraw.Draw(colour).text((8, 5), "A", fill=(240, 30, 30))
    colour_ref = _canonical(store, colour)
    result = extract_glyph_assets(
        store, colour_ref, {"text_id": "colour", "bbox": [2, 2, 28, 20]}, operation_id="colour"
    )
    assert result["status"] in {"estimated", "low_confidence"}
    assert result["glyph_mask_ref"] is not None and result["glyph_image_ref"] is not None

    dark = Image.new("RGB", (32, 24), (20, 20, 24))
    ImageDraw.Draw(dark).text((8, 5), "A", fill=(240, 220, 20))
    dark_ref = _canonical(store, dark)
    dark_result = extract_glyph_assets(
        store, dark_ref, {"text_id": "yellow", "bbox": [2, 2, 28, 20]}, operation_id="yellow"
    )
    assert dark_result["glyph_mask_ref"] is not None

    uniform = _canonical(store, Image.new("RGB", (32, 24), (240, 30, 30)))
    uniform_result = extract_glyph_assets(
        store, uniform, {"text_id": "uniform", "bbox": [2, 2, 28, 20]}, operation_id="uniform"
    )
    assert uniform_result["status"] == "unavailable"

    complex_colour = Image.new("RGB", (32, 24), "white")
    complex_pixels = complex_colour.load()
    for row in range(24):
        for column in range(32):
            complex_pixels[column, row] = (240, 40, 40) if (column + row) % 2 else (30, 60, 230)
    complex_ref = _canonical(store, complex_colour)
    complex_result = extract_glyph_assets(
        store, complex_ref, {"text_id": "complex", "bbox": [2, 2, 28, 20]}, operation_id="complex"
    )
    assert complex_result["status"] == "unavailable"
    assert any("background colour" in item for item in complex_result["uncertainties"])

    rectangle = _canonical(store, Image.new("RGB", (32, 24), "white"))
    rectangle_result = extract_glyph_assets(
        store, rectangle, {"text_id": "rect", "bbox": [2, 2, 28, 20]}, operation_id="rect"
    )
    assert rectangle_result["glyph_mask_ref"] is None


def test_contour_scope_shape_and_text_geometry_are_strict(store):
    canonical = _canonical(store, Image.new("RGB", (20, 20), "white"))
    wrong_role = store.put("text-task", "mask", _png(Image.new("L", (20, 20))), role="edit_mask")
    with pytest.raises(PipelineError) as raised:
        extract_glyph_assets(
            store,
            canonical,
            {"text_id": "x", "bbox": [1, 1, 10, 10]},
            operation_id="strict-1",
            segmentation_mask_ref=wrong_role,
        )
    assert raised.value.code == "artifact_scope"
    wrong_size = store.put("text-task", "mask2", _png(Image.new("L", (8, 8))), role="contour_mask")
    with pytest.raises(PipelineError) as raised:
        extract_glyph_assets(
            store,
            canonical,
            {"text_id": "x", "bbox": [1, 1, 10, 10]},
            operation_id="strict-2",
            segmentation_mask_ref=wrong_size,
        )
    assert raised.value.code == "artifact_changed"
    with pytest.raises(PipelineError):
        extract_glyph_assets(store, canonical, {"text_id": "x", "bbox": [0, 0, 0, 4]}, operation_id="strict-3")
