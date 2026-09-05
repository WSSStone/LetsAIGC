from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from letsaigc.schemas.ui import UIResourceLimits
from letsaigc.ui_analysis.coordinates import checked_box, checked_polygon, map_point
from letsaigc.ui_analysis.normalize import make_views, normalize


@pytest.mark.parametrize("orientation", range(1, 9))
def test_all_exif_orientations_and_pixel_center_inverse(ui_store, orientation):
    pixels = np.arange(3 * 5 * 4, dtype=np.uint8).reshape(3, 5, 4)
    image = Image.fromarray(pixels, "RGBA")
    exif = Image.Exif()
    exif[274] = orientation
    output = BytesIO()
    image.save(output, format="PNG", exif=exif)
    ref = ui_store.put("image-task", "intake", output.getvalue(), role="original", media_type="image/png")
    result, transform = normalize(ui_store, ref, "normalize", UIResourceLimits())
    with Image.open(BytesIO(ui_store.read(result.canonical_ref))) as canonical:
        actual = np.asarray(canonical)
    expected = {
        1: pixels,
        2: pixels[:, ::-1],
        3: pixels[::-1, ::-1],
        4: pixels[::-1],
        5: pixels.transpose(1, 0, 2),
        6: np.rot90(pixels, -1),
        7: pixels.transpose(1, 0, 2)[::-1, ::-1],
        8: np.rot90(pixels, 1),
    }[orientation]
    assert np.array_equal(actual, expected)
    for x, y in ((0.5, 0.5), (4.5, 2.5)):
        target = map_point((x, y), transform["original_to_canonical"])
        assert map_point(target, transform["canonical_to_original"]) == pytest.approx((x, y))
        assert np.array_equal(actual[int(target[1]), int(target[0])], pixels[int(y), int(x)])
    assert ui_store.read(ref) == output.getvalue()


def test_view_tiles_cover_odd_size_and_round_trip_without_using_thumbnail_for_crops(ui_store):
    image = Image.new("RGB", (2077, 1091), (8, 17, 26))
    data = BytesIO()
    image.save(data, format="PNG")
    source = ui_store.put("views", "intake", data.getvalue(), role="original", media_type="image/png")
    canonical, _ = normalize(ui_store, source, "normalize", UIResourceLimits())
    views = make_views(ui_store, canonical, "views", UIResourceLimits())
    assert len([view for view in views if view.kind == "ocr_tile"]) == 6
    overview = next(view for view in views if view.kind == "overview")
    assert max(overview.width, overview.height) == 1536
    coverage = np.zeros((1091, 2077), dtype=bool)
    for view in views:
        x1, y1, x2, y2 = view.crop
        if view.kind == "ocr_tile":
            coverage[y1:y2, x1:x2] = True
            assert max(view.width, view.height) <= 1024
        point = map_point((x1, y1), view.forward)
        assert map_point(point, view.inverse) == pytest.approx((x1, y1))
    assert coverage.all()
    with pytest.raises(ValueError, match="tiles"):
        make_views(ui_store, canonical, "too-many", UIResourceLimits(ocr_max_tiles=1))


@pytest.mark.parametrize("box", [(0, 0, 0, 1), (-1, 0, 2, 2), (0, 0, 11, 10), (0, 0, float("nan"), 1)])
def test_unsafe_boxes_rejected_before_cropping(box):
    with pytest.raises(ValueError):
        checked_box(box, 10, 10)


def test_rounding_and_polygon_rejection():
    assert checked_box((1.2, 2.2, 8.1, 9.1), 10, 10) == (1, 2, 9, 10)
    assert checked_polygon([(0, 0), (10, 0), (10, 10), (0, 10)], 10, 10)
    for polygon in (
        [(0, 0), (2, 2), (1, 1)],
        [(0, 0), (5, 5), (0, 5), (5, 0)],
        [(0, 0), (1, 0), (1, 0)],
        [(0, 0), (11, 1), (1, 1)],
    ):
        with pytest.raises(ValueError):
            checked_polygon(polygon, 10, 10)


def test_checked_polygon_promotes_numpy_coordinates_before_intersection_math():
    polygon = np.asarray([[0, 0], [1024, 0], [1024, 1024], [0, 1024]], dtype=np.int32)

    assert checked_polygon(polygon, 1024, 1024) == [
        (0.0, 0.0),
        (1024.0, 0.0),
        (1024.0, 1024.0),
        (0.0, 1024.0),
    ]
