from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import psutil

from ..paths import find_repo_root, local_path
from ..policy.gates import sha256_file
from ..schemas import LicenseLane, RunGovernance, RunManifest, RunOutput


def _git(command: list[str]) -> str | None:
    completed = subprocess.run(
        ["git", *command],
        cwd=find_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def environment_snapshot() -> dict[str, Any]:
    return {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "machine": platform.machine(),
        "processor": platform.processor(),
        "ram_bytes": psutil.virtual_memory().total,
        "conda_environment": os.getenv("CONDA_DEFAULT_ENV"),
    }


def create_manifest(
    *,
    kind: str,
    parameters: dict[str, Any],
    license_lanes: list[LicenseLane],
    source: dict[str, Any] | None = None,
) -> RunManifest:
    git_commit = _git(["rev-parse", "HEAD"])
    git_status = _git(["status", "--short"])
    merged_source = {
        "git_commit": git_commit,
        "git_dirty": bool(git_status),
        "git_status_sha256": hashlib.sha256((git_status or "").encode()).hexdigest(),
        **(source or {}),
    }
    return RunManifest(
        run_id=f"{kind}-{uuid.uuid4().hex[:16]}",
        kind=kind,
        status="created",
        source=merged_source,
        parameters=parameters,
        environment=environment_snapshot(),
        governance=RunGovernance(
            license_lanes=license_lanes,
            validations={"contract": False, "hashes": False, "provenance": True},
        ),
    )


def manifest_path(run_id: str) -> Path:
    return local_path("runs", run_id, "manifest.json")


def save_manifest(manifest: RunManifest) -> Path:
    path = manifest_path(manifest.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_manifest(run_id: str) -> RunManifest:
    return RunManifest.model_validate_json(manifest_path(run_id).read_text(encoding="utf-8"))


def add_output(manifest: RunManifest, path: Path) -> None:
    manifest.outputs.append(
        RunOutput(
            path=str(path.resolve()), sha256=sha256_file(path), size_bytes=path.stat().st_size
        )
    )


def file_sha256_or_none(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None
