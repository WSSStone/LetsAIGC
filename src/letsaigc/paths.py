from __future__ import annotations

import os
from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path:
    override = os.getenv("LETSAIGC_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "configs").is_dir():
            return candidate
    package_root = Path(__file__).resolve().parents[2]
    if (package_root / "pyproject.toml").is_file():
        return package_root
    raise RuntimeError("Could not locate the LetsAIGC repository root")


def local_path(*parts: str) -> Path:
    path = find_repo_root() / ".local"
    return path.joinpath(*parts)
