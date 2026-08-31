from __future__ import annotations

import os
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
    run_name: str, parameters: dict[str, Any], metrics: dict[str, float]
) -> str | None:
    try:
        import mlflow
    except ImportError:
        return None
    root = find_repo_root()
    artifact_root = local_path("mlflow", "artifacts")
    artifact_root.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(tracking_uri())
    mlflow.set_experiment("letsaigc-local")
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params({key: str(value) for key, value in parameters.items()})
        if metrics:
            mlflow.log_metrics(metrics)
        mlflow.set_tag("repository", str(root))
        return run.info.run_id
