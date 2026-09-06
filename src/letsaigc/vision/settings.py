"""Shared OCR lock selection for the planner, worker and isolated service."""

import platform
from pathlib import Path

import yaml

from ..paths import find_repo_root


def load_ocr_settings(root: Path | None = None):
    root = root or find_repo_root()
    config = yaml.safe_load((root / "configs/runtime/vision.yaml").read_text(encoding="utf-8"))["ocr"]
    platform_id = f"{platform.system()}-{platform.machine()}"
    lock_path = config.get("platform_locks", {}).get(platform_id, config["lock"])
    lock = yaml.safe_load((root / lock_path).read_text(encoding="utf-8"))
    return config, lock
