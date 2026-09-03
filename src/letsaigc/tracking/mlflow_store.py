from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..paths import find_repo_root, local_path


def tracking_uri() -> str:
    override = os.getenv("LETSAIGC_MLFLOW_URI")
    if override:
        return override
    database = local_path("mlflow", "mlflow.db")
    database.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{database.as_posix()}"


def log_manifest(
    run_name: str,
    parameters: dict[str, Any],
    metrics: dict[str, float],
    *,
    artifacts: list[Path] | None = None,
    parent_run_id: str | None = None,
    existing_run_id: str | None = None,
) -> str | None:
    try:
        import mlflow
    except ImportError:
        return None
    root = find_repo_root()
    artifact_root = local_path("mlflow", "artifacts")
    artifact_root.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(tracking_uri())
    experiment_name = "letsaigc-local"
    if mlflow.get_experiment_by_name(experiment_name) is None:
        mlflow.create_experiment(experiment_name, artifact_location=artifact_root.resolve().as_uri())
    mlflow.set_experiment(experiment_name)
    tags = {"repository": str(root)}
    if parent_run_id:
        tags["mlflow.parentRunId"] = parent_run_id
    start_arguments: dict[str, Any]
    if existing_run_id:
        start_arguments = {"run_id": existing_run_id}
    else:
        start_arguments = {"run_name": run_name, "tags": tags}
    with mlflow.start_run(**start_arguments) as run:
        mlflow.log_params({key: str(value) for key, value in parameters.items()})
        if metrics:
            mlflow.log_metrics(metrics)
        for artifact in artifacts or []:
            if artifact.is_file():
                mlflow.log_artifact(str(artifact))
        return run.info.run_id
