from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..config import load_catalog
from ..paths import find_repo_root
from ..policy.gates import sha256_file
from ..schemas import AdapterRecord, LicenseLane


def dataset_dvc_revision(dataset: str) -> str | None:
    root = find_repo_root()
    dvc_pointer = root / f"{Path(dataset).as_posix()}.dvc"
    if not dvc_pointer.is_file():
        return None
    pointer = yaml.safe_load(dvc_pointer.read_text(encoding="utf-8")) or {}
    outputs = pointer.get("outs", [])
    if not outputs:
        return None
    return outputs[0].get("md5") or outputs[0].get("hash")


def register_adapter(
    adapter: Path,
    config_path: Path,
    config: dict[str, Any],
    mlflow_or_run_id: str,
) -> Path:
    root = find_repo_root()
    record = AdapterRecord(
        id=f"{config['id']}-{adapter.stem}",
        base_model_id=config["base_model"],
        base_model_sha256=[
            item.sha256 for item in load_catalog().by_id()[config["base_model"]].files
        ],
        adapter_path=str(adapter.resolve()),
        adapter_sha256=sha256_file(adapter),
        dataset_path=config["dataset_dir"],
        dataset_dvc_rev=dataset_dvc_revision(config["dataset_dir"]),
        training_config_path=str(config_path),
        training_config_sha256=sha256_file(config_path),
        effective_parameters=config,
        trainer_revision="37a1cbbc5725ed2a3575506e7bd2001c9908ac92",
        mlflow_run_id=mlflow_or_run_id,
        license_lane=LicenseLane.production,
    )
    target = root / "registry/adapters" / f"{record.id}.json"
    target.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return target
