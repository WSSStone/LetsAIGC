"""Segmentation and glyph extraction exchange canonical artifact references."""

import json
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw

from letsaigc.assets.store import ArtifactStore
from letsaigc.ui_analysis.normalize import png
from letsaigc.ui_analysis.text_assets import extract_glyph_assets
from letsaigc.vision.segmentation import render_segmentation_assets


def test_contour_artifact_can_refine_colored_text_without_extracting_its_panel(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    image = Image.new("RGB", (40, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((10, 8, 10, 22), fill="red", width=3)
    draw.line((22, 8, 22, 22), fill="red", width=3)
    draw.line((10, 15, 22, 15), fill="red", width=3)
    canonical = store.put("task", "input", png(image), role="canonical", media_type="image/png")
    probability = np.zeros((30, 40), dtype=np.float32)
    probability[5:26, 5:29] = 1
    assets = render_segmentation_assets(image, probability, (5, 5, 29, 26))
    contour = store.put("task", "sam", assets["contour_mask"], role="contour_mask", media_type="image/png",
                        source_ids=[canonical.artifact_id])
    result = extract_glyph_assets(store, canonical, {"text_id": "label", "bbox": [5, 5, 29, 26]},
                                  operation_id="glyph", segmentation_mask_ref=contour)
    assert result["status"] in {"estimated", "low_confidence"}
    with Image.open(BytesIO(store.read(result["glyph_image_ref"]))) as glyph:
        visible = np.asarray(glyph)[..., 3] > 0
        assert visible.any() and not visible.all()
        assert np.all(np.asarray(glyph)[visible][:, :3] == [255, 0, 0])
    with Image.open(BytesIO(store.read(result["rect_crop_ref"]))) as rect:
        assert rect.tobytes() == image.crop((5, 5, 29, 26)).tobytes()
    provenance = json.loads(store.read(result["provenance_ref"]))
    assert provenance["canonical_ref"]["sha256"] == canonical.sha256
    assert provenance["contour_mask_ref"]["sha256"] == contour.sha256
