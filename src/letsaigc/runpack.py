from __future__ import annotations

import json
import zipfile
from pathlib import Path

from .config import load_workflow_contract
from .paths import find_repo_root, local_path
from .tracking import load_manifest


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
