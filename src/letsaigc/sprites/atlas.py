from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..errors import ValidationError
from ..policy.gates import sha256_file
from ..schemas import (
    SpriteAnchor,
    SpriteFrameRecord,
    SpriteProfile,
    SpriteRect,
    SpriteSheetMetadata,
)
from .processor import NormalizedFrame


@dataclass(frozen=True)
class AtlasResult:
    path: Path
    metadata: SpriteSheetMetadata


def pack_sprite_sheet(
    frames: list[NormalizedFrame],
    frame_paths: list[Path],
    profile: SpriteProfile,
    output_path: Path,
    source_sha256: str,
    *,
    source_run_id: str | None = None,
    warnings: list[str] | None = None,
    validations: dict[str, bool] | None = None,
) -> AtlasResult:
    if len(frames) != len(frame_paths) or not frames:
        raise ValidationError("Sprite atlas requires matching non-empty frames and paths")
    columns = math.ceil(math.sqrt(len(frames)))
    rows = math.ceil(len(frames) / columns)
    width = columns * profile.canvas_width
    height = rows * profile.canvas_height
    if width > profile.atlas.max_width or height > profile.atlas.max_height:
        raise ValidationError(f"Sprite atlas {width}x{height} exceeds configured maximum")
    sheet = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    records: list[SpriteFrameRecord] = []
    for index, (frame, frame_path) in enumerate(zip(frames, frame_paths, strict=True)):
        x = (index % columns) * profile.canvas_width
        y = (index // columns) * profile.canvas_height
        sheet.alpha_composite(frame.image, (x, y))
        raw_width, raw_height = frame.raw_size
        records.append(
            SpriteFrameRecord(
                index=index,
                filename=frame_path.name,
                rect=SpriteRect(x=x, y=y, width=profile.canvas_width, height=profile.canvas_height),
                duration_ms=1000.0 / profile.fps,
                anchor=SpriteAnchor(
                    x=frame.anchor[0],
                    y=frame.anchor[1],
                    normalized_x=frame.anchor[0] / profile.canvas_width,
                    normalized_y=frame.anchor[1] / profile.canvas_height,
                ),
                raw_anchor=SpriteAnchor(
                    x=frame.raw_anchor[0],
                    y=frame.raw_anchor[1],
                    normalized_x=frame.raw_anchor[0] / raw_width,
                    normalized_y=frame.raw_anchor[1] / raw_height,
                ),
                sha256=sha256_file(frame_path),
                foreground_fraction=frame.foreground_fraction,
                border_contact_fraction=frame.border_contact_fraction,
                raw_border_contact_fraction=frame.raw_border_contact_fraction,
            )
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)
    metadata = SpriteSheetMetadata(
        profile_id=profile.id,
        sheet_filename=output_path.name,
        sheet_width=width,
        sheet_height=height,
        columns=columns,
        rows=rows,
        source_run_id=source_run_id,
        source_sha256=source_sha256,
        frames=records,
        warnings=warnings or [],
        validations=validations or {"frames": True, "anchor": True, "atlas": True},
    )
    return AtlasResult(output_path, metadata)
