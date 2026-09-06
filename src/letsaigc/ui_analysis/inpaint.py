"""Deterministic mask preparation and CPU composition for UI inpainting.

The module deliberately contains no model or provider code.  A prepared mask
is a grayscale image where zero means keep and nonzero means edit.  The same
resize and padding transform is applied to the source image and its mask;
generation output can then be mapped back to the canonical canvas before the
final CPU composite.
"""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

_CHANNELS = {"red": "R", "green": "G", "blue": "B", "alpha": "A"}


def _positive_pair(value: tuple[int, int] | list[int], name: str) -> tuple[int, int]:
    if len(value) != 2 or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in value):
        raise ValueError(f"{name} must contain two positive integers")
    return int(value[0]), int(value[1])


def _padding(value: tuple[int, int, int, int] | list[int]) -> tuple[int, int, int, int]:
    if len(value) != 4 or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value):
        raise ValueError("pad must contain four nonnegative integers")
    return tuple(int(item) for item in value)  # type: ignore[return-value]


def _box(value: tuple[int, int, int, int] | list[int], width: int, height: int) -> tuple[int, int, int, int]:
    if len(value) != 4 or any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise ValueError("target_roi must contain four integer coordinates")
    x1, y1, x2, y2 = (int(item) for item in value)
    if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1 or x2 > width or y2 > height:
        raise ValueError("target_roi is outside the mask canvas")
    return x1, y1, x2, y2


def _channel_image(mask: Image.Image, channel: str) -> Image.Image:
    if channel not in _CHANNELS:
        raise ValueError(f"Unsupported mask channel: {channel}")
    if mask.mode == "L" or mask.mode == "1":
        if channel != "red":
            raise ValueError("Single-channel masks only support the red channel")
        return mask.convert("L")
    if channel == "alpha" and "A" not in mask.getbands():
        raise ValueError("Mask has no alpha channel")
    if _CHANNELS[channel] not in mask.getbands():
        raise ValueError(f"Mask has no {channel} channel")
    return mask.getchannel(_CHANNELS[channel])


def resize_and_pad_image(
    image: Image.Image,
    *,
    resize: tuple[int, int] | list[int],
    pad: tuple[int, int, int, int] | list[int],
    fill: Any = None,
    resample: Image.Resampling = Image.Resampling.LANCZOS,
) -> Image.Image:
    """Apply a frozen resize/pad transform while retaining image mode."""

    target_size = _positive_pair(resize, "resize")
    left, top, right, bottom = _padding(pad)
    resized = image.resize(target_size, resample=resample)
    if fill is None:
        if image.mode == "RGBA":
            fill = (0, 0, 0, 0)
        elif image.mode == "RGB":
            fill = (0, 0, 0)
        else:
            fill = 0
    canvas = Image.new(image.mode, (target_size[0] + left + right, target_size[1] + top + bottom), fill)
    canvas.paste(resized, (left, top))
    return canvas


def prepare_edit_mask(
    mask: Image.Image,
    *,
    channel: str = "red",
    polarity: str = "white_is_edit",
    resize: tuple[int, int] | list[int] | None = None,
    pad: tuple[int, int, int, int] | list[int] = (0, 0, 0, 0),
    target_roi: tuple[int, int, int, int] | list[int] | None = None,
    keep_mask: Image.Image | None = None,
    dilation_px: int = 0,
    feather_px: int = 0,
) -> Image.Image:
    """Normalize, grow, feather and constrain an edit mask.

    The production contract fixes ``channel=red`` and ``polarity=white_is_edit``;
    accepting another polarity would silently invert the approved edit scope.
    """

    if not isinstance(mask, Image.Image):
        raise TypeError("mask must be a PIL image")
    if polarity != "white_is_edit":
        raise ValueError("Only white_is_edit mask polarity is supported")
    if isinstance(dilation_px, bool) or not isinstance(dilation_px, int) or dilation_px < 0:
        raise ValueError("dilation_px must be a nonnegative integer")
    if isinstance(feather_px, bool) or not isinstance(feather_px, int) or feather_px < 0:
        raise ValueError("feather_px must be a nonnegative integer")

    gray = _channel_image(mask, channel)
    target_size = gray.size if resize is None else _positive_pair(resize, "resize")
    gray = gray.resize(target_size, resample=Image.Resampling.NEAREST)
    if dilation_px:
        gray = gray.filter(ImageFilter.MaxFilter(dilation_px * 2 + 1))
    if feather_px:
        gray = gray.filter(ImageFilter.GaussianBlur(float(feather_px)))

    left, top, right, bottom = _padding(pad)
    canvas = Image.new("L", (target_size[0] + left + right, target_size[1] + top + bottom), 0)
    canvas.paste(gray, (left, top))
    if target_roi is not None:
        x1, y1, x2, y2 = _box(target_roi, canvas.width, canvas.height)
        roi = Image.new("L", canvas.size, 0)
        ImageDraw.Draw(roi).rectangle((x1, y1, x2 - 1, y2 - 1), fill=255)
        canvas = ImageChops.multiply(canvas, roi)
    if keep_mask is not None:
        if not isinstance(keep_mask, Image.Image) or keep_mask.mode not in {"L", "1"}:
            raise ValueError("keep_mask must be a grayscale image")
        if keep_mask.size != canvas.size:
            raise ValueError("keep_mask dimensions must match the prepared mask")
        keep = keep_mask.convert("L").point(lambda value: 0 if value > 0 else 255)
        canvas = ImageChops.multiply(canvas, keep)
    return canvas


def compose_inpaint(
    original: Image.Image,
    generated: Image.Image | None,
    mask: Image.Image,
) -> Image.Image:
    """Composite generated pixels only where the final mask permits editing."""

    if not isinstance(original, Image.Image) or not isinstance(mask, Image.Image):
        raise TypeError("original and mask must be PIL images")
    if mask.mode != "L" or mask.size != original.size:
        raise ValueError("Final mask must be grayscale and match the original size")
    if mask.getbbox() is None:
        if generated is None:
            return original.copy()
        if generated.size != original.size or generated.mode != original.mode:
            raise ValueError("Generated image must match original dimensions and mode")
        return original.copy()
    if generated is None:
        raise ValueError("A non-empty edit mask requires a generated image")
    if generated.size != original.size or generated.mode != original.mode:
        raise ValueError("Generated image must match original dimensions and mode")
    return Image.composite(generated, original, mask)


def restore_to_canonical(
    canonical: Image.Image,
    generated_prepared: Image.Image,
    canonical_mask: Image.Image,
    binding: Any,
) -> Image.Image:
    """Map a prepared generation back to canonical coordinates and composite it."""

    if not isinstance(canonical, Image.Image) or not isinstance(generated_prepared, Image.Image):
        raise TypeError("canonical and generated_prepared must be PIL images")
    if generated_prepared.size != (binding.width, binding.height):
        raise ValueError("Generated image dimensions do not match the frozen prepared binding")
    if canonical.size != (binding.canonical_width, binding.canonical_height):
        raise ValueError("Canonical image dimensions do not match the frozen binding")
    left, top, right, bottom = _padding(binding.pad)
    crop_x1, crop_y1, crop_x2, crop_y2 = _box(
        binding.crop, binding.canonical_width, binding.canonical_height
    )
    prepared_crop = generated_prepared.crop((left, top, left + binding.resize[0], top + binding.resize[1]))
    mapped = prepared_crop.resize((crop_x2 - crop_x1, crop_y2 - crop_y1), Image.Resampling.LANCZOS)
    generated_canonical = canonical.copy()
    generated_canonical.paste(mapped, (crop_x1, crop_y1))
    return compose_inpaint(canonical, generated_canonical, canonical_mask)
