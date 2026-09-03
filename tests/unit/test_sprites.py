from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from letsaigc.errors import ValidationError
from letsaigc.schemas import SpriteProfile
from letsaigc.sprites.atlas import pack_sprite_sheet
from letsaigc.sprites.processor import apply_chroma_key, normalize_frames
from letsaigc.sprites.validation import validate_normalized_frames


def _profile() -> SpriteProfile:
    return SpriteProfile.model_validate(
        {
            "schema_version": 1,
            "id": "test-rgba",
            "canvas_width": 64,
            "canvas_height": 64,
            "fps": 12,
            "max_frames": 8,
            "margin_fraction": 0.05,
            "pixel_art": False,
            "background": {
                "color": "#00FF00",
                "transparent_delta": 18,
                "opaque_delta": 45,
                "despill_strength": 0.75,
                "edge_feather_px": 1,
            },
            "anchor": {
                "mode": "alpha_bottom_center",
                "target_x": 0.5,
                "target_y": 0.95,
                "max_raw_drift_fraction": 0.08,
                "max_error_px": 1,
            },
            "validation": {
                "min_foreground_fraction": 0.01,
                "max_foreground_fraction": 0.8,
                "max_border_contact_fraction": 0.005,
            },
            "atlas": {"max_width": 512, "max_height": 512, "layout": "near_square_row_major"},
        }
    )


def _frame(x: int) -> Image.Image:
    image = Image.new("RGB", (64, 64), "#00FF00")
    draw = ImageDraw.Draw(image)
    draw.rectangle((x, 20, x + 15, 55), fill="#E02020")
    return image


def test_chroma_key_and_anchor_normalization() -> None:
    profile = _profile()
    keyed = [apply_chroma_key(_frame(x), profile.background) for x in (8, 16, 24)]
    assert keyed[0].mode == "RGBA"
    assert keyed[0].getpixel((0, 0))[3] == 0
    normalized = normalize_frames(keyed, profile)
    assert all(item.image.size == (64, 64) for item in normalized)
    assert all(abs(item.anchor[0] - 32) <= 1 for item in normalized)
    assert all(abs(item.anchor[1] - 60.8) <= 1 for item in normalized)
    diagnostics = validate_normalized_frames(normalized, profile)
    assert diagnostics.valid


def test_pixel_art_profile_uses_hard_alpha() -> None:
    profile = _profile().model_copy(
        update={
            "pixel_art": True,
            "background": _profile().background.model_copy(update={"hard_alpha": True, "edge_feather_px": 0}),
        }
    )
    keyed = apply_chroma_key(_frame(8).resize((63, 63)), profile.background)
    assert set(np.unique(np.asarray(keyed.getchannel("A")))) <= {0, 255}


def test_wrong_chroma_key_fails_foreground_coverage() -> None:
    profile = _profile()
    wrong_key = profile.background.model_copy(update={"color": "#0000FF"})
    normalized = normalize_frames([apply_chroma_key(_frame(8), wrong_key)], profile)
    diagnostics = validate_normalized_frames(normalized, profile)
    assert diagnostics.valid is False
    assert any("foreground fraction" in failure for failure in diagnostics.failures)


def test_source_edge_residue_is_not_hidden_by_canvas_margin() -> None:
    profile = _profile()
    keyed = apply_chroma_key(_frame(8), profile.background)
    ImageDraw.Draw(keyed).rectangle((0, 0, 63, 63), outline=(0, 160, 0, 255), width=1)
    diagnostics = validate_normalized_frames(normalize_frames([keyed], profile), profile)
    assert diagnostics.valid is False
    assert any("raw border contact" in failure for failure in diagnostics.failures)


def test_atlas_is_row_major_and_hashes_frames(tmp_path: Path) -> None:
    profile = _profile()
    frames = normalize_frames([apply_chroma_key(_frame(x), profile.background) for x in (8, 16, 24)], profile)
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    paths = []
    for index, frame in enumerate(frames, start=1):
        path = frame_dir / f"{index:06d}.png"
        frame.image.save(path)
        paths.append(path)
    result = pack_sprite_sheet(frames, paths, profile, tmp_path / "sheet.png", "a" * 64)
    assert result.metadata.columns == 2
    assert result.metadata.rows == 2
    assert result.metadata.frames[2].rect.x == 0
    assert result.path.is_file()

    too_small = profile.model_copy(
        update={"atlas": profile.atlas.model_copy(update={"max_width": 64, "max_height": 64})}
    )
    with pytest.raises(ValidationError, match="exceeds configured maximum"):
        pack_sprite_sheet(frames, paths, too_small, tmp_path / "too-large.png", "a" * 64)
