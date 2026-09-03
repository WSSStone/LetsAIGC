from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw

from ..errors import RuntimeExecutionError
from ..policy.gates import sha256_file
from .tools import probe_media, run_ffmpeg


def create_contact_sheet(
    video: Path,
    destination: Path,
    *,
    sample_count: int = 6,
    tile_width: int = 320,
) -> dict:
    if not video.is_file() or sample_count < 2 or sample_count > 12:
        raise RuntimeExecutionError("Contact-sheet input or sample count is invalid")
    media = probe_media(video)
    if not media.duration_seconds or not media.width or not media.height:
        raise RuntimeExecutionError("Video lacks duration or dimensions for contact-sheet evaluation")
    destination.mkdir(parents=True, exist_ok=True)
    frame_dir = destination / "contact-frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    interval = media.duration_seconds / sample_count
    black = run_ffmpeg(
        ["-i", str(video), "-vf", "blackdetect=d=0.5:pix_th=0.10", "-an", "-f", "null", os.devnull],
        timeout_seconds=max(60, round(media.duration_seconds * 20)),
    )
    black_durations = [float(value) for value in re.findall(r"black_duration:([0-9.]+)", black["stderr"])]
    ffmpeg = run_ffmpeg(
        [
            "-y",
            "-i",
            str(video),
            "-vf",
            f"fps=1/{interval:.8f},scale={tile_width}:-2:flags=lanczos",
            "-frames:v",
            str(sample_count),
            str(frame_dir / "%03d.png"),
        ],
        timeout_seconds=max(60, round(media.duration_seconds * 20)),
    )
    frames = sorted(frame_dir.glob("*.png"))
    if len(frames) < 2:
        raise RuntimeExecutionError("FFmpeg produced too few contact-sheet frames")
    frames = frames[:sample_count]
    opened = [Image.open(path).convert("RGB") for path in frames]
    try:
        tile_height = max(image.height for image in opened)
        columns = math.ceil(math.sqrt(len(opened)))
        rows = math.ceil(len(opened) / columns)
        sheet = Image.new("RGB", (columns * tile_width, rows * (tile_height + 24)), "black")
        draw = ImageDraw.Draw(sheet)
        for index, image in enumerate(opened):
            x = (index % columns) * tile_width
            y = (index // columns) * (tile_height + 24)
            sheet.paste(image, (x, y))
            draw.text((x + 6, y + tile_height + 4), f"frame {index + 1}", fill="white")
        output = destination / "contact-sheet.jpg"
        sheet.save(output, quality=90, optimize=True)
    finally:
        for image in opened:
            image.close()
    evidence = {
        "source": str(video.resolve()),
        "source_sha256": sha256_file(video),
        "media": media.model_dump(mode="json"),
        "sample_count": len(frames),
        "black_durations_seconds": black_durations,
        "black_frames_passed": not any(value >= 0.5 for value in black_durations),
        "interval_seconds": interval,
        "frame_sha256": [sha256_file(path) for path in frames],
        "contact_sheet": str(output.resolve()),
        "contact_sheet_sha256": sha256_file(output),
        "ffmpeg": {
            "arguments": ffmpeg["arguments"],
            "stderr_sha256": hashlib.sha256(ffmpeg["stderr"].encode()).hexdigest(),
        },
    }
    (destination / "contact-sheet.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return evidence
