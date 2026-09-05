from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil

from ..config import load_catalog, load_training_config
from ..errors import ReadinessError, RuntimeExecutionError
from ..models import ModelManager
from ..paths import find_repo_root, local_path
from ..policy import assert_model_allowed
from ..tracking.manifest import add_output, create_manifest, save_manifest
from ..tracking.mlflow_store import log_manifest
from .adapters import dataset_dvc_revision, register_adapter

FLAG_KEYS = {
    "gradient_checkpointing",
    "sdpa",
    "cache_latents",
    "cache_latents_to_disk",
    "cache_text_encoder_outputs",
    "cache_text_encoder_outputs_to_disk",
}


def _base_model_path(model_id: str) -> Path:
    model = load_catalog().by_id()[model_id]
    assert_model_allowed(model, operation="train")
    path = local_path("models", model.files[0].target_path)
    if not path.is_file():
        raise ReadinessError(f"Base model is missing: {path}")
    ModelManager().verify_model(model)
    return path


def build_training_command(
    config: dict[str, Any], train_data_dir: Path | None = None
) -> list[str]:
    ignored = {"schema_version", "id", "base_model", "resource_budget", "train_text_encoder"}
    option_names = {"dataset_dir": "train_data_dir"}
    command = [
        "conda",
        "run",
        "--no-capture-output",
        "-n",
        "letsaigc-train-sdxl",
        "python",
        str(local_path("runtime", "sd-scripts", "sdxl_train_network.py")),
        "--pretrained_model_name_or_path",
        str(_base_model_path(config["base_model"])),
    ]
    for key, value in config.items():
        if key in ignored:
            continue
        option = f"--{option_names.get(key, key)}"
        if key == "dataset_dir" and train_data_dir is not None:
            value = train_data_dir
        if key in FLAG_KEYS:
            if value:
                command.append(option)
        elif isinstance(value, bool):
            if value:
                command.append(option)
        else:
            command.extend([option, str(value)])
    if not config.get("train_text_encoder", False):
        command.append("--network_train_unet_only")
    return command


def _stage_training_dataset(config: dict[str, Any]) -> Path:
    source = find_repo_root() / config["dataset_dir"]
    revision = dataset_dvc_revision(config["dataset_dir"]) or "unversioned"
    target = local_path("cache", "training-datasets", f"{config['id']}-{revision}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("*.npz"),
    )
    return target


def _validate_adapter(adapter: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "conda",
            "run",
            "--no-capture-output",
            "-n",
            "letsaigc-train-sdxl",
            "python",
            str(find_repo_root() / "scripts/validate_adapter.py"),
            str(adapter),
        ],
        cwd=find_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        report = {"valid": False, "message": completed.stderr.strip() or completed.stdout.strip()}
    if completed.returncode != 0 or not report.get("valid"):
        raise RuntimeExecutionError(f"Adapter numerical validation failed: {report}")
    return report


def _preflight(config: dict[str, Any]) -> None:
    dataset = find_repo_root() / config["dataset_dir"]
    if not dataset.is_dir() or not any(dataset.rglob("*.png")) and not any(dataset.rglob("*.jpg")):
        raise ReadinessError(f"Training dataset has no PNG/JPG images: {dataset}")
    trainer = local_path("runtime", "sd-scripts", "sdxl_train_network.py")
    if not trainer.is_file():
        raise ReadinessError("sd-scripts is not deployed; bootstrap train first")
    budget = config["resource_budget"]
    if psutil.virtual_memory().available < float(budget["ram_gib"]) * 1024**3 * 0.5:
        raise ReadinessError("Available RAM is below half of the declared training budget")
    output = find_repo_root() / config["output_dir"]
    output.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output).free < float(budget["free_disk_gib"]) * 1024**3:
        raise ReadinessError("Free disk is below the declared training budget")


def run_sdxl_lora(config_path: Path) -> dict:
    config = load_training_config(config_path).model_dump(mode="python")
    _preflight(config)
    train_data_dir = _stage_training_dataset(config)
    command = build_training_command(config, train_data_dir=train_data_dir)
    base_model = load_catalog().by_id()[config["base_model"]]
    manifest = create_manifest(
        kind="training",
        parameters={**config, "effective_command": command},
        license_lanes=[load_catalog().by_id()[config["base_model"]].license.lane],
        source={
            "trainer_commit": "37a1cbbc5725ed2a3575506e7bd2001c9908ac92",
            "base_model_sha256": [item.sha256 for item in base_model.files],
        },
    )
    manifest.governance.validations["contract"] = True
    manifest.status = "running"
    save_manifest(manifest)
    started_at_ns = time.time_ns()
    completed = subprocess.run(command, cwd=find_repo_root(), check=False)
    if completed.returncode != 0:
        manifest.status = "failed"
        manifest.error = {"type": "TrainerExit", "returncode": completed.returncode}
        save_manifest(manifest)
        raise RuntimeExecutionError(f"Trainer exited with {completed.returncode}")
    output_dir = find_repo_root() / config["output_dir"]
    candidates = sorted(
        (
            path
            for path in output_dir.glob("*.safetensors")
            if path.stat().st_mtime_ns >= started_at_ns
        ),
        key=lambda path: path.stat().st_mtime_ns,
    )
    if not candidates:
        manifest.status = "failed"
        manifest.error = {"type": "MissingAdapterOutput"}
        save_manifest(manifest)
        raise RuntimeExecutionError(
            "Trainer returned success but no safetensors adapter was produced"
        )
    adapter = candidates[-1]
    try:
        numerical_report = _validate_adapter(adapter)
    except Exception as exc:
        manifest.status = "failed"
        manifest.error = {"type": type(exc).__name__, "message": str(exc)}
        manifest.governance.validations["numerical"] = False
        save_manifest(manifest)
        raise
    add_output(manifest, adapter)
    manifest.governance.validations.update({"hashes": True, "numerical": True})
    manifest.tracking["adapter_validation"] = numerical_report
    mlflow_run_id = log_manifest(
        manifest.run_id,
        {"kind": "training", "config": config["id"], "base_model": config["base_model"]},
        {
            "steps": float(config["max_train_steps"]),
            "adapter_size_mib": adapter.stat().st_size / 1024**2,
        },
    )
    if mlflow_run_id:
        manifest.tracking["mlflow_run_id"] = mlflow_run_id
    manifest.status = "succeeded"
    save_manifest(manifest)
    record = register_adapter(adapter, config_path, config, mlflow_run_id)
    result = json.loads(manifest.model_dump_json())
    result["adapter_record"] = str(record)
    return result
