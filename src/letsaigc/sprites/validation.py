from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..schemas import SpriteProfile
from .processor import NormalizedFrame


@dataclass(frozen=True)
class SpriteDiagnostics:
    valid: bool
    failures: list[str]
    warnings: list[str]


def validate_normalized_frames(frames: list[NormalizedFrame], profile: SpriteProfile) -> SpriteDiagnostics:
    failures: list[str] = []
    warnings: list[str] = []
    if not frames:
        failures.append("no frames")
        return SpriteDiagnostics(False, failures, warnings)
    if len(frames) > profile.max_frames:
        failures.append(f"frame count {len(frames)} exceeds {profile.max_frames}")
    expected_size = (profile.canvas_width, profile.canvas_height)
    target = np.array([profile.anchor.target_x * profile.canvas_width, profile.anchor.target_y * profile.canvas_height])
    raw = np.array([item.raw_anchor for item in frames], dtype=np.float32)
    median = np.median(raw, axis=0)
    drift = float(np.linalg.norm(raw - median, axis=1).max()) / max(profile.canvas_width, profile.canvas_height)
    if drift > profile.anchor.max_raw_drift_fraction:
        warnings.append(f"raw anchor drift {drift:.4f} exceeds {profile.anchor.max_raw_drift_fraction}")
    for index, frame in enumerate(frames):
        if frame.image.size != expected_size or frame.image.mode != "RGBA":
            failures.append(f"frame {index} has inconsistent size or mode")
        if not (
            profile.validation.min_foreground_fraction
            <= frame.foreground_fraction
            <= profile.validation.max_foreground_fraction
        ):
            failures.append(f"frame {index} foreground fraction {frame.foreground_fraction:.4f} is out of range")
        if frame.border_contact_fraction > profile.validation.max_border_contact_fraction:
            failures.append(f"frame {index} border contact {frame.border_contact_fraction:.4f} exceeds limit")
        if frame.raw_border_contact_fraction > profile.validation.max_border_contact_fraction:
            failures.append(
                f"frame {index} raw border contact {frame.raw_border_contact_fraction:.4f} exceeds limit"
            )
        if float(np.linalg.norm(np.array(frame.anchor) - target)) > profile.anchor.max_error_px:
            failures.append(f"frame {index} anchor is outside tolerance")
    return SpriteDiagnostics(not failures, failures, warnings)
