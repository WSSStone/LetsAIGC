from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..policy.gates import sha256_file
from ..schemas import DramaProject, DramaShot


def shot_fingerprint(shot: DramaShot, source_sha256: str, project: DramaProject) -> str:
    payload = {
        "schema": 1,
        "shot": shot.model_dump(mode="json", exclude_none=True),
        "source_sha256": source_sha256,
        "output": {"width": project.width, "height": project.height, "fps": project.fps},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cached_shot(cache_dir: Path, fingerprint: str) -> Path | None:
    video = cache_dir / f"{fingerprint}.mp4"
    digest = cache_dir / f"{fingerprint}.sha256"
    if not video.is_file() or not digest.is_file():
        return None
    expected = digest.read_text(encoding="utf-8").strip()
    if len(expected) != 64 or sha256_file(video) != expected:
        return None
    return video


def record_cached_shot(video: Path, fingerprint: str) -> None:
    digest = video.with_name(f"{fingerprint}.sha256")
    digest.write_text(f"{sha256_file(video)}\n", encoding="utf-8")
