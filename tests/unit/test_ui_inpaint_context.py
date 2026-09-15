"""Deterministic context geometry; no providers or approvals."""
import pytest

from letsaigc.ui_analysis.editing import inpaint_context_geometry


@pytest.mark.parametrize('box,size', [
    ((1734, 184, 1757, 208), (1920, 1080)),
    ((0, 0, 10, 10), (1920, 1080)),
    ((1910, 1070, 1920, 1080), (1920, 1080)),
    ((1, 1, 7, 7), (8, 8)),
    ((0, 0, 1920, 1080), (1920, 1080)),
])
def test_context_contains_edit_and_stays_inside_source(box, size):
    crop, resize = inpaint_context_geometry(box, size)
    assert 0 <= crop[0] <= box[0] < box[2] <= crop[2] <= size[0]
    assert 0 <= crop[1] <= box[1] < box[3] <= crop[3] <= size[1]
    assert all(8 <= edge <= 1024 and edge % 8 == 0 for edge in resize)
    assert max(resize) >= 512
    assert (crop, resize) == inpaint_context_geometry(box, size)


def test_blue_marker_has_context_not_24_pixel_input():
    crop, resize = inpaint_context_geometry((1734, 184, 1757, 208), (1920, 1080))
    assert crop == (1617, 68, 1873, 324)
    assert resize == (512, 512)
