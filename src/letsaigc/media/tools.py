from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..errors import ReadinessError, RuntimeExecutionError, ValidationError
from ..schemas import MediaMetadata


@dataclass(frozen=True)
class MediaToolInfo:
    ffmpeg_path: str
    ffprobe_path: str
    ffmpeg_version: str
    ffprobe_version: str
    has_png: bool
    has_h264_decoder: bool
    has_libx264_encoder: bool

    @property
    def ready(self) -> bool:
        return self.has_png and self.has_h264_decoder and self.has_libx264_encoder

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ready": self.ready}


def parse_fraction(value: str | int | float | None) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            result = float(numerator) / denominator_value
        else:
            result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _resolved_command(name: str) -> str:
    result = shutil.which(name)
    if not result:
        raise ReadinessError(f"Required media command is not available on PATH: {name}")
    return str(Path(result).resolve())


def _run(arguments: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    if not arguments:
        raise ValidationError("Media command arguments may not be empty")
    try:
        return subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeExecutionError(
            f"Media command failed to start: {Path(arguments[0]).name}",
            details={"error": str(exc), "arguments": arguments},
        ) from exc


def _version_line(command: str) -> str:
    completed = _run([command, "-version"], timeout_seconds=10)
    if completed.returncode:
        raise ReadinessError(
            f"Media command returned exit {completed.returncode}: {Path(command).name}",
            details={"stderr": completed.stderr[-2000:]},
        )
    return (completed.stdout or completed.stderr).splitlines()[0].strip()


def inspect_media_tools() -> MediaToolInfo:
    ffmpeg = _resolved_command("ffmpeg")
    ffprobe = _resolved_command("ffprobe")
    formats = _run([ffmpeg, "-hide_banner", "-formats"], timeout_seconds=15)
    codecs = _run([ffmpeg, "-hide_banner", "-codecs"], timeout_seconds=15)
    encoders = _run([ffmpeg, "-hide_banner", "-encoders"], timeout_seconds=15)
    for completed, label in ((formats, "formats"), (codecs, "codecs"), (encoders, "encoders")):
        if completed.returncode:
            raise ReadinessError(f"FFmpeg could not enumerate {label}", details={"stderr": completed.stderr[-2000:]})
    format_text = f"{formats.stdout}\n{formats.stderr}".lower()
    codec_text = f"{codecs.stdout}\n{codecs.stderr}".lower()
    encoder_text = f"{encoders.stdout}\n{encoders.stderr}".lower()
    return MediaToolInfo(
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        ffmpeg_version=_version_line(ffmpeg),
        ffprobe_version=_version_line(ffprobe),
        has_png=" png" in format_text or "image2" in format_text,
        has_h264_decoder=" h264" in codec_text,
        has_libx264_encoder="libx264" in encoder_text,
    )


def require_media_tools() -> MediaToolInfo:
    info = inspect_media_tools()
    if not info.ready:
        raise ReadinessError("FFmpeg build lacks required PNG/H.264/libx264 capabilities", details=info.as_dict())
    return info


def probe_media(path: Path, *, timeout_seconds: int = 30) -> MediaMetadata:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise ValidationError(f"Media file does not exist: {source}")
    ffprobe = _resolved_command("ffprobe")
    completed = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        ],
        timeout_seconds=timeout_seconds,
    )
    if completed.returncode:
        raise RuntimeExecutionError(
            f"ffprobe rejected media: {source.name}",
            details={"stderr": completed.stderr[-4000:], "returncode": completed.returncode},
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeExecutionError("ffprobe returned malformed JSON", details={"error": str(exc)}) from exc
    streams = payload.get("streams", [])
    stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    if stream is None:
        stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if stream is None:
        raise RuntimeExecutionError("Media contains no supported video or audio stream")
    fps = parse_fraction(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
    duration = parse_fraction(stream.get("duration")) or parse_fraction(payload.get("format", {}).get("duration"))
    raw_frames = stream.get("nb_frames")
    frame_count = int(raw_frames) if str(raw_frames).isdigit() else None
    if frame_count is None and fps and duration:
        frame_count = max(1, round(fps * duration))
    pixel_format = stream.get("pix_fmt")
    alpha_formats = {"rgba", "argb", "bgra", "abgr", "yuva420p", "yuva422p", "yuva444p", "gbrap"}
    mime_type = mimetypes.guess_type(source.name)[0] or (
        "video/unknown" if stream.get("codec_type") == "video" else "audio/unknown"
    )
    return MediaMetadata(
        mime_type=mime_type,
        codec=stream.get("codec_name"),
        pixel_format=pixel_format,
        width=stream.get("width"),
        height=stream.get("height"),
        frame_count=frame_count,
        fps=fps,
        duration_seconds=duration,
        has_alpha=pixel_format in alpha_formats if pixel_format else None,
        has_audio=any(item.get("codec_type") == "audio" for item in streams),
    )


def run_ffmpeg(arguments: list[str], *, timeout_seconds: int = 600) -> dict[str, Any]:
    info = require_media_tools()
    normalized = [info.ffmpeg_path, "-hide_banner", "-nostdin", *[str(item) for item in arguments]]
    completed = _run(normalized, timeout_seconds=timeout_seconds)
    result = {
        "arguments": normalized,
        "returncode": completed.returncode,
        "stderr": completed.stderr[-8000:],
        "tool": info.as_dict(),
    }
    if completed.returncode:
        raise RuntimeExecutionError("FFmpeg execution failed", details=result)
    return result
