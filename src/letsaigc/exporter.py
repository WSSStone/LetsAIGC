from __future__ import annotations

import shutil
from pathlib import Path

from .errors import PolicyError, ValidationError
from .policy import evaluate_export, verify_sha256
from .schemas import LicenseLane
from .tracking import load_manifest


def export_run(run_id: str, destination: Path, lane: LicenseLane) -> dict:
    manifest = load_manifest(run_id)
    decision = evaluate_export(manifest, lane=lane)
    if not decision.allowed:
        raise PolicyError("Production export denied", details={"reasons": decision.reasons})
    target = destination.expanduser().resolve() / run_id
    if target.exists():
        raise ValidationError(f"Export target already exists: {target}")
    target.mkdir(parents=True)
    copied = []
    for output in manifest.outputs:
        source = Path(output.path)
        verify_sha256(source, output.sha256)
        result = target / source.name
        shutil.copy2(source, result)
        copied.append(str(result))
    shutil.copy2(Path(manifest_path := manifest_source_path(run_id)), target / "manifest.json")
    return {
        "run_id": run_id,
        "destination": str(target),
        "files": copied,
        "manifest": manifest_path,
    }


def manifest_source_path(run_id: str) -> Path:
    from .tracking.manifest import manifest_path

    return manifest_path(run_id)
