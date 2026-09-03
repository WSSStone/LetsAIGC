from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .config import load_typed, load_workflow_contract
from .errors import ValidationError
from .media.importer import import_video
from .paths import find_repo_root, local_path
from .schemas import VideoImportMetadata, VideoJob
from .tracking import load_manifest, save_manifest


def build_runpack(run_id: str) -> Path:
    root = find_repo_root()
    manifest = load_manifest(run_id)
    target = local_path("runpacks", f"{run_id}.zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    portable = manifest.model_dump(mode="json")
    portable["outputs"] = [
        {"filename": Path(item.path).name, "sha256": item.sha256, "size_bytes": item.size_bytes}
        for item in manifest.outputs
    ]
    portable["environment"].pop("conda_environment", None)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.portable.json", json.dumps(portable, indent=2))
        workflow_id = manifest.parameters.get("workflow_id")
        if workflow_id:
            for relative in (
                f"workflows/api/{workflow_id}.json",
                f"workflows/contracts/{workflow_id}.yaml",
            ):
                path = root / relative
                if path.is_file():
                    archive.write(path, relative)
            contract = load_workflow_contract(workflow_id)
            for dependency in contract.adapters:
                record = root / dependency.record
                if record.is_file():
                    archive.write(record, dependency.record)
        training_id = manifest.parameters.get("id") if manifest.kind == "training" else None
        if training_id:
            training_config = root / f"configs/training/{training_id}.yaml"
            if training_config.is_file():
                archive.write(training_config, f"configs/training/{training_id}.yaml")
            dataset_dir = manifest.parameters.get("dataset_dir")
            if dataset_dir:
                dvc_pointer = root / f"{Path(dataset_dir).as_posix()}.dvc"
                if dvc_pointer.is_file():
                    archive.write(dvc_pointer, dvc_pointer.relative_to(root).as_posix())
        archive.write(root / "configs/models/catalog.yaml", "configs/models/catalog.yaml")
        archive.write(
            root / "configs/policies/license-policy.yaml", "configs/policies/license-policy.yaml"
        )
        archive.writestr(
            "README.txt",
            "Provider-neutral metadata only. Supply approved model weights and secrets at the destination.\n",
        )
    return target


@dataclass(frozen=True)
class JobRunpack:
    path: Path
    fingerprint: str


def canonical_fingerprint(value: dict[str, Any] | VideoJob) -> str:
    import hashlib

    job = value if isinstance(value, VideoJob) else VideoJob.model_validate(value)
    payload = job.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _assert_portable(value: Any, *, key: str = "root") -> None:
    secret_tokens = {"token", "secret", "password", "api_key", "apikey", "credential"}
    if isinstance(value, dict):
        for child_key, child in value.items():
            normalized = str(child_key).lower().replace("-", "_")
            if any(token in normalized for token in secret_tokens):
                raise ValidationError(f"Video job may not contain secrets: {key}.{child_key}")
            _assert_portable(child, key=f"{key}.{child_key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_portable(child, key=f"{key}[{index}]")
    elif isinstance(value, str):
        if PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute():
            raise ValidationError(f"Video job may not contain absolute paths: {key}")
        if Path(value).suffix.lower() in {".safetensors", ".ckpt", ".pt", ".pth", ".bin"}:
            raise ValidationError(f"Video job may not embed or reference weight files: {key}")


def build_job_runpack(job_path: Path) -> JobRunpack:
    root = find_repo_root()
    job = load_typed(job_path, VideoJob)
    payload = job.model_dump(mode="json")
    _assert_portable(payload)
    fingerprint = canonical_fingerprint(payload)
    target = local_path("runpacks", f"{job.id}-{fingerprint[:12]}.zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    result_contract = {
        "schema_version": 1,
        "request_fingerprint": fingerprint,
        "expected_output": payload["expected_output"],
        "required_metadata": VideoImportMetadata.model_json_schema(),
    }
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("video-job.json", json.dumps(payload, indent=2, ensure_ascii=False))
        archive.writestr("result-contract.json", json.dumps(result_contract, indent=2))
        for relative in (
            f"workflows/api/{job.workflow}.json",
            f"workflows/contracts/{job.workflow}.yaml",
            "configs/models/catalog.yaml",
            "configs/policies/license-policy.yaml",
        ):
            source = root / relative
            if source.is_file():
                archive.write(source, relative)
        archive.writestr(
            "README.txt",
            "Provider-neutral request metadata only. Supply approved weights and credentials "
            "at the execution destination.\n",
        )
    return JobRunpack(target, fingerprint)


def _read_runpack_json(runpack: Path, name: str) -> dict[str, Any]:
    source = runpack.expanduser().resolve()
    try:
        if source.is_dir():
            return json.loads((source / name).read_text(encoding="utf-8"))
        with zipfile.ZipFile(source) as archive:
            return json.loads(archive.read(name))
    except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise ValidationError(f"Invalid video runpack: missing or malformed {name}") from exc


def ingest_job_result(
    runpack: Path,
    result_path: Path,
    metadata_path: Path,
) -> dict[str, Any]:
    job_payload = _read_runpack_json(runpack, "video-job.json")
    result_contract = _read_runpack_json(runpack, "result-contract.json")
    job = VideoJob.model_validate(job_payload)
    fingerprint = canonical_fingerprint(job)
    if result_contract.get("request_fingerprint") != fingerprint:
        raise ValidationError("Runpack request fingerprint does not match its result contract")
    metadata = load_typed(metadata_path, VideoImportMetadata)
    if metadata.runpack_fingerprint != fingerprint:
        raise ValidationError("Result metadata does not match the runpack request fingerprint")
    source = result_path.expanduser().resolve()
    if source.suffix.lower() not in job.expected_output.allowed_extensions:
        raise ValidationError(f"Runpack result extension is not allowed: {source.suffix}")
    result = import_video(source, metadata_path)
    manifest = load_manifest(result["run_id"])
    media = manifest.outputs[0].media if manifest.outputs else None
    violations: list[str] = []
    expected = job.expected_output
    if media:
        if expected.width and media.width != expected.width:
            violations.append(f"width {media.width} != {expected.width}")
        if expected.height and media.height != expected.height:
            violations.append(f"height {media.height} != {expected.height}")
        if expected.max_frames and media.frame_count and media.frame_count > expected.max_frames:
            violations.append(f"frame count {media.frame_count} > {expected.max_frames}")
    if violations:
        manifest.status = "failed"
        manifest.governance.validations["contract"] = False
        manifest.error = {"type": "ValidationError", "message": "; ".join(violations)}
        save_manifest(manifest)
        raise ValidationError("Runpack result violates its output contract", details={"violations": violations})
    manifest.source["video_job_id"] = job.id
    manifest.source["video_job_fingerprint"] = fingerprint
    save_manifest(manifest)
    return json.loads(manifest.model_dump_json())
