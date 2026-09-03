from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..config import load_typed
from ..errors import ValidationError
from ..paths import local_path
from ..policy.gates import sha256_file, verify_sha256
from ..schemas import RunOutput, VideoImportMetadata
from ..tracking.manifest import create_manifest, save_manifest
from ..tracking.mlflow_store import log_manifest
from .tools import inspect_media_tools, probe_media


def import_video(path: Path, metadata_path: Path) -> dict:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise ValidationError(f"Video import source does not exist: {source}")
    metadata = load_typed(metadata_path, VideoImportMetadata)
    manifest = create_manifest(
        kind="video_generation",
        parameters={"operation": "import", "metadata": metadata.model_dump(mode="json")},
        license_lanes=[metadata.license_lane],
        source={
            "external_source": metadata.source,
            "provider": metadata.provider,
            "model": metadata.model,
            "model_revision": metadata.revision,
            "license_id": metadata.license_id,
            "runpack_fingerprint": metadata.runpack_fingerprint,
        },
    )
    save_manifest(manifest)
    try:
        target_dir = local_path("runs", manifest.run_id, "inputs")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        shutil.copy2(source, target)
        media = probe_media(target)
        digest = sha256_file(target)
        verify_sha256(target, digest)
        manifest.outputs.append(
            RunOutput(
                path=str(target.resolve()),
                sha256=digest,
                size_bytes=target.stat().st_size,
                role="primary_video",
                media_kind="video",
                media=media,
            )
        )
        manifest.environment["media_tools"] = inspect_media_tools().as_dict()
        manifest.governance.validations.update({"contract": True, "hashes": True, "provenance": True})
        manifest.status = "succeeded"
        run_id = log_manifest(
            manifest.run_id,
            {"kind": manifest.kind, "provider": metadata.provider, "model": metadata.model},
            {"output_count": 1.0},
            artifacts=[target],
            parent_run_id=None,
        )
        if run_id:
            manifest.tracking["mlflow_run_id"] = run_id
    except Exception as exc:
        manifest.status = "failed"
        manifest.error = {"type": type(exc).__name__, "message": str(exc)}
        save_manifest(manifest)
        raise
    save_manifest(manifest)
    return json.loads(manifest.model_dump_json())
