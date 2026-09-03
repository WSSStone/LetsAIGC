from __future__ import annotations

from pathlib import Path

from letsaigc.media import create_contact_sheet, run_ffmpeg


def test_contact_sheet_is_deterministic_for_same_video(tmp_path: Path) -> None:
    video = tmp_path / "source.mp4"
    run_ffmpeg(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=96x64:rate=8:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ]
    )
    first = create_contact_sheet(video, tmp_path / "first", sample_count=4, tile_width=96)
    second = create_contact_sheet(video, tmp_path / "second", sample_count=4, tile_width=96)
    assert first["contact_sheet_sha256"] == second["contact_sheet_sha256"]
    assert first["frame_sha256"] == second["frame_sha256"]
    assert first["sample_count"] == 4
