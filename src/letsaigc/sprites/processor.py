from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

from ..errors import ValidationError
from ..schemas import ChromaKeyConfig, SpriteProfile


@dataclass(frozen=True)
class NormalizedFrame:
    image: Image.Image
    raw_anchor: tuple[float, float]
    raw_size: tuple[int, int]
    anchor: tuple[float, float]
    foreground_fraction: float
    border_contact_fraction: float
    raw_border_contact_fraction: float


def _hex_rgb(value: str) -> np.ndarray:
    return np.array([int(value[index : index + 2], 16) for index in (1, 3, 5)], dtype=np.float32)


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    value = rgb.astype(np.float32) / 255.0
    value = np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)
    xyz = (
        value
        @ np.array(
            [[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]],
            dtype=np.float32,
        ).T
    )
    xyz = xyz / np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
    delta = 6 / 29
    transformed = np.where(xyz > delta**3, np.cbrt(xyz), xyz / (3 * delta**2) + 4 / 29)
    return np.stack(
        [
            116 * transformed[..., 1] - 16,
            500 * (transformed[..., 0] - transformed[..., 1]),
            200 * (transformed[..., 1] - transformed[..., 2]),
        ],
        axis=-1,
    )


def apply_chroma_key(image: Image.Image, config: ChromaKeyConfig) -> Image.Image:
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    key_rgb = _hex_rgb(config.color)
    key_lab = _rgb_to_lab(key_rgb.reshape(1, 1, 3))[0, 0]
    distance = np.linalg.norm(_rgb_to_lab(rgb) - key_lab, axis=-1)
    span = config.opaque_delta - config.transparent_delta
    if span <= 0:
        raise ValidationError("Chroma opaque_delta must exceed transparent_delta")
    alpha = np.clip((distance - config.transparent_delta) / span, 0.0, 1.0)
    if config.hard_alpha:
        alpha = (alpha >= 0.5).astype(np.float32)
    keyed = rgb.astype(np.float32)
    key_channel = int(np.argmax(key_rgb))
    other_channels = [index for index in range(3) if index != key_channel]
    comparison = np.maximum(keyed[..., other_channels[0]], keyed[..., other_channels[1]])
    spill = np.maximum(0.0, keyed[..., key_channel] - comparison)
    keyed[..., key_channel] -= spill * config.despill_strength * (1.0 - alpha)
    alpha_image = Image.fromarray(np.round(alpha * 255).astype(np.uint8), mode="L")
    if config.edge_feather_px:
        alpha_image = alpha_image.filter(ImageFilter.GaussianBlur(config.edge_feather_px))
    result = Image.fromarray(np.clip(keyed, 0, 255).astype(np.uint8), mode="RGB").convert("RGBA")
    result.putalpha(alpha_image)
    return result


def _foreground_metrics(image: Image.Image) -> tuple[float, float]:
    alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
    foreground_mask = alpha >= 128
    visible_mask = alpha >= 8
    foreground = int(foreground_mask.sum())
    visible = int(visible_mask.sum())
    if foreground == 0 or visible == 0:
        return 0.0, 0.0
    border = np.concatenate(
        (
            visible_mask[0, :],
            visible_mask[-1, :],
            visible_mask[1:-1, 0],
            visible_mask[1:-1, -1],
        )
    )
    return foreground / foreground_mask.size, int(border.sum()) / visible


def normalize_frames(images: list[Image.Image], profile: SpriteProfile) -> list[NormalizedFrame]:
    if not images:
        raise ValidationError("Sprite processing produced no frames")
    bounds: list[tuple[int, int, int, int]] = []
    raw_anchors: list[tuple[float, float]] = []
    raw_border_contacts: list[float] = []
    for image in images:
        rgba = image.convert("RGBA")
        bbox = rgba.getchannel("A").getbbox()
        if bbox is None:
            raise ValidationError("Sprite frame contains no visible foreground")
        bounds.append(bbox)
        raw_anchors.append(((bbox[0] + bbox[2]) / 2.0, float(bbox[3])))
        raw_border_contacts.append(_foreground_metrics(rgba)[1])
    max_width = max(right - left for left, _, right, _ in bounds)
    max_height = max(bottom - top for _, top, _, bottom in bounds)
    available_width = profile.canvas_width * (1 - 2 * profile.margin_fraction)
    available_height = profile.canvas_height * (1 - 2 * profile.margin_fraction)
    scale = min(available_width / max_width, available_height / max_height)
    if profile.pixel_art:
        scale = max(1.0, float(int(scale))) if scale >= 1 else 1.0 / max(1, int(round(1 / scale)))
    resample = Image.Resampling.NEAREST if profile.pixel_art else Image.Resampling.LANCZOS
    target_x = profile.anchor.target_x * profile.canvas_width
    target_y = profile.anchor.target_y * profile.canvas_height
    normalized: list[NormalizedFrame] = []
    for image, bbox, raw_anchor, raw_border_contact in zip(
        images, bounds, raw_anchors, raw_border_contacts, strict=True
    ):
        crop = image.convert("RGBA").crop(bbox)
        width = max(1, round(crop.width * scale))
        height = max(1, round(crop.height * scale))
        resized = crop.resize((width, height), resample=resample)
        left = round(target_x - width / 2)
        top = round(target_y - height)
        if left < 0 or top < 0 or left + width > profile.canvas_width or top + height > profile.canvas_height:
            raise ValidationError("Normalized sprite does not fit the configured canvas")
        canvas = Image.new("RGBA", (profile.canvas_width, profile.canvas_height), (0, 0, 0, 0))
        canvas.alpha_composite(resized, (left, top))
        foreground, border = _foreground_metrics(canvas)
        normalized.append(
            NormalizedFrame(
                image=canvas,
                raw_anchor=raw_anchor,
                raw_size=image.size,
                anchor=(left + width / 2.0, float(top + height)),
                foreground_fraction=foreground,
                border_contact_fraction=border,
                raw_border_contact_fraction=raw_border_contact,
            )
        )
    return normalized
