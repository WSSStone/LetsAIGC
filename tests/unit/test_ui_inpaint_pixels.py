"""T025 deterministic mask preparation and CPU composition contracts."""

from __future__ import annotations

import importlib

import pytest
from PIL import Image


def _inpaint_module():
    """T026's module is the only allowed pre-implementation xfail."""

    try:
        return importlib.import_module("letsaigc.ui_analysis.inpaint")
    except ModuleNotFoundError as exc:
        if exc.name == "letsaigc.ui_analysis.inpaint":
            pytest.xfail("T026: CPU inpaint mask module is not implemented")
        raise


def _prepare(mask: Image.Image, **kwargs) -> Image.Image:
    module = _inpaint_module()
    return module.prepare_edit_mask(mask, **kwargs)


def _compose(original: Image.Image, generated: Image.Image | None, mask: Image.Image) -> Image.Image:
    module = _inpaint_module()
    return module.compose_inpaint(original, generated, mask)


def test_red_channel_and_white_is_edit_survive_resize_and_pad_transform() -> None:
    # The green channel deliberately disagrees with red.  A transparent or
    # implicitly selected channel would therefore edit the wrong two pixels.
    mask = Image.new("RGB", (2, 2), (0, 0, 0))
    mask.putpixel((0, 0), (255, 0, 0))
    mask.putpixel((1, 0), (0, 255, 0))
    mask.putpixel((0, 1), (0, 0, 255))
    prepared = _prepare(
        mask,
        channel="red",
        polarity="white_is_edit",
        resize=(4, 4),
        pad=(1, 2, 1, 0),
    )
    assert prepared.mode == "L"
    assert prepared.size == (6, 6)
    assert prepared.getbbox() == (1, 2, 3, 4)
    assert all(value in {0, 255} for value in prepared.getdata())

    with pytest.raises((ValueError, TypeError)):
        _prepare(mask, channel="red", polarity="black_is_edit")


def test_dilation_and_feather_are_clipped_to_target_roi_and_subtract_keep() -> None:
    mask = Image.new("L", (9, 9), 0)
    mask.putpixel((4, 4), 255)
    keep = Image.new("L", (9, 9), 0)
    keep.putpixel((6, 4), 255)
    prepared = _prepare(
        mask,
        channel="red",
        polarity="white_is_edit",
        target_roi=(2, 2, 7, 7),
        keep_mask=keep,
        dilation_px=2,
        feather_px=1,
    )
    assert prepared.size == mask.size
    assert prepared.getpixel((4, 4)) > 0
    assert prepared.getpixel((6, 4)) == 0
    assert any(0 < value < 255 for value in prepared.getdata())
    for y in range(prepared.height):
        for x in range(prepared.width):
            if not (2 <= x < 7 and 2 <= y < 7):
                assert prepared.getpixel((x, y)) == 0


def test_empty_final_mask_returns_original_without_edit_pixels() -> None:
    original = Image.new("RGB", (5, 4), (11, 22, 33))
    empty = Image.new("L", original.size, 0)
    prepared = _prepare(empty, channel="red", polarity="white_is_edit")
    assert prepared.getbbox() is None
    # ``generated=None`` is the explicit no-provider/no-generation fast path.
    assert _compose(original, None, prepared).tobytes() == original.tobytes()
    nonempty = Image.new("L", original.size, 0)
    nonempty.putpixel((2, 2), 255)
    with pytest.raises((ValueError, TypeError)):
        _compose(original, None, nonempty)


@pytest.mark.parametrize(
    ("original", "generated", "mask"),
    [
        (Image.new("RGB", (5, 4)), Image.new("RGB", (4, 4)), Image.new("L", (5, 4))),
        (Image.new("RGB", (5, 4)), Image.new("RGB", (5, 4)), Image.new("L", (4, 4))),
        (Image.new("RGB", (5, 4)), Image.new("RGBA", (5, 4)), Image.new("L", (5, 4))),
    ],
)
def test_compose_rejects_any_size_or_mode_mismatch(original, generated, mask) -> None:
    with pytest.raises((ValueError, TypeError)):
        _compose(original, generated, mask)


def test_zero_mask_pixels_are_copied_byte_for_byte() -> None:
    original = Image.new("RGB", (7, 5))
    original.putdata([(x * 29 % 256, y * 47 % 256, (x * 11 + y * 13) % 256) for y in range(5) for x in range(7)])
    generated = Image.new("RGB", original.size, (255, 1, 2))
    mask = Image.new("L", original.size, 0)
    for y in range(1, 4):
        for x in range(2, 5):
            mask.putpixel((x, y), 255)
    result = _compose(original, generated, mask)

    assert result.mode == original.mode
    for y in range(original.height):
        for x in range(original.width):
            if mask.getpixel((x, y)) == 0:
                assert result.getpixel((x, y)) == original.getpixel((x, y))
            else:
                assert result.getpixel((x, y)) == generated.getpixel((x, y))
