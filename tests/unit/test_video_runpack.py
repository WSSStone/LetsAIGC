from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import yaml

from letsaigc.errors import ValidationError
from letsaigc.media import run_ffmpeg
from letsaigc.runpack import build_job_runpack, canonical_fingerprint, ingest_job_result


def _job() -> dict:
    return {
        "schema_version": 1,
        "id": "wan-cloud-test",
        "workflow": "wan21-t2v-smoke",
        "inputs": {"prompt": "walking knight", "seed": 7},
        "models": ["wan21-t2v-1.3b"],
        "references": [],
        "resource_budget": {
            "vram_gib": 24,
            "ram_gib": 32,
            "free_disk_gib": 20,
            "timeout_seconds": 3600,
        },
        "expected_output": {
            "media_kind": "video",
            "allowed_extensions": [".mp4"],
            "width": 512,
            "height": 512,
            "max_frames": 81,
        },
    }


def test_canonical_fingerprint_is_order_independent() -> None:
    left = _job()
    right = json.loads(json.dumps(left, sort_keys=True))
    assert canonical_fingerprint(left) == canonical_fingerprint(right)


def test_job_runpack_contains_contract_not_secrets_or_weights(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    (root / "configs/models").mkdir(parents=True)
    (root / "configs/policies").mkdir(parents=True)
    (root / "workflows/api").mkdir(parents=True)
    (root / "workflows/contracts").mkdir(parents=True)
    (root / "configs/models/catalog.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (root / "configs/policies/license-policy.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (root / "workflows/api/wan21-t2v-smoke.json").write_text("{}", encoding="utf-8")
    (root / "workflows/contracts/wan21-t2v-smoke.yaml").write_text("id: wan21-t2v-smoke\n", encoding="utf-8")
    job_path = root / "job.yaml"
    job_path.write_text(yaml.safe_dump(_job()), encoding="utf-8")
    monkeypatch.setenv("LETSAIGC_ROOT", str(root))

    result = build_job_runpack(job_path)
    with zipfile.ZipFile(result.path) as archive:
        names = set(archive.namelist())
        payload = archive.read("video-job.json").decode()
    assert {"video-job.json", "result-contract.json", "README.txt"} <= names
    assert "token" not in payload.lower()
    assert not any(name.endswith((".safetensors", ".ckpt", ".pt", ".pth")) for name in names)
    assert result.fingerprint == canonical_fingerprint(_job())


@pytest.mark.parametrize(
    "inputs",
    [
        {"reference": "C:/private/reference.png"},
        {"api_token": "do-not-package"},
        {"checkpoint": "weights/model.safetensors"},
    ],
)
def test_job_runpack_rejects_paths_secrets_and_weight_references(
    tmp_path: Path,
    monkeypatch,
    inputs: dict[str, str],
) -> None:
    root = tmp_path / "repo"
    (root / "configs/models").mkdir(parents=True)
    (root / "configs/policies").mkdir(parents=True)
    job = _job()
    job["inputs"] = inputs
    job_path = root / "job.yaml"
    job_path.write_text(yaml.safe_dump(job), encoding="utf-8")
    monkeypatch.setenv("LETSAIGC_ROOT", str(root))
    with pytest.raises(ValidationError):
        build_job_runpack(job_path)


def test_runpack_result_ingest_validates_fingerprint_and_media(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "repo"
    (root / "configs/models").mkdir(parents=True)
    (root / "configs/policies").mkdir(parents=True)
    (root / "workflows/api").mkdir(parents=True)
    (root / "workflows/contracts").mkdir(parents=True)
    (root / "configs/models/catalog.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (root / "configs/policies/license-policy.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    job = _job()
    job["expected_output"].update({"width": 64, "height": 64, "max_frames": 12})
    job_path = root / "job.yaml"
    job_path.write_text(yaml.safe_dump(job), encoding="utf-8")
    monkeypatch.setenv("LETSAIGC_ROOT", str(root))
    monkeypatch.setattr("letsaigc.media.importer.log_manifest", lambda *args, **kwargs: None)
    runpack = build_job_runpack(job_path)
    result_path = root / "result.mp4"
    run_ffmpeg(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:d=0.5:r=12",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(result_path),
        ]
    )
    metadata = {
        "schema_version": 1,
        "source": "fixture-provider-job",
        "provider": "fixture",
        "model": "fixture-video-model",
        "revision": "fixture-revision",
        "license_id": "Apache-2.0",
        "license_lane": "production",
        "commercial_use": "test fixture",
        "runpack_fingerprint": runpack.fingerprint,
    }
    metadata_path = root / "result.yaml"
    metadata_path.write_text(yaml.safe_dump(metadata), encoding="utf-8")

    manifest = ingest_job_result(runpack.path, result_path, metadata_path)
    assert manifest["status"] == "succeeded"
    assert manifest["source"]["video_job_fingerprint"] == runpack.fingerprint
    assert manifest["outputs"][0]["media"]["width"] == 64
