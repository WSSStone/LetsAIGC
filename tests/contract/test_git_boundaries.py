from __future__ import annotations

import subprocess

import pytest

from letsaigc.paths import find_repo_root


@pytest.mark.parametrize(
    "relative",
    [
        ".doc/probe.md",
        ".local/models/probe.safetensors",
        ".codegraph/index",
        ".env",
        "outputs/probe.png",
        "probe.ckpt",
        ".dvc/cache/probe",
    ],
)
def test_local_or_binary_path_is_git_ignored(relative: str) -> None:
    completed = subprocess.run(
        ["git", "check-ignore", "--quiet", relative], cwd=find_repo_root(), check=False
    )
    assert completed.returncode == 0, relative


@pytest.mark.parametrize(
    "relative",
    [
        "configs/models/catalog.yaml",
        "workflows/api/sd15-smoke.json",
        "datasets/example.dvc",
        ".env.example",
    ],
)
def test_engineering_or_pointer_path_is_not_ignored(relative: str) -> None:
    completed = subprocess.run(
        ["git", "check-ignore", "--quiet", relative], cwd=find_repo_root(), check=False
    )
    assert completed.returncode == 1, relative
