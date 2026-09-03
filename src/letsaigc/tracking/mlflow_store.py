from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import get_setting
from ..paths import find_repo_root, local_path


def tracking_uri() -> str:
    override = get_setting("MLFLOW_URI")
    if override:
        return override
    database = local_path("mlflow", "mlflow.db")
    database.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{database.as_posix()}"


def artifact_root() -> Path:
    """Resolve MLFLOW_ARTIFACT_ROOT (relative to the repo root) or fall back to .local."""
    configured = get_setting("MLFLOW_ARTIFACT_ROOT")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = find_repo_root() / path
    else:
        path = local_path("mlflow", "artifacts")
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_manifest(
    run_name: str,
    parameters: dict[str, Any],
    metrics: dict[str, float],
    *,
    artifacts: list[Path] | None = None,
    parent_run_id: str | None = None,
    existing_run_id: str | None = None,
    idempotency_key: str | None = None,
) -> str | None:
    try:
        import mlflow
    except ImportError:
        return None
    root = find_repo_root()
    artifact_dir = artifact_root()
    mlflow.set_tracking_uri(tracking_uri())
    experiment_name = "letsaigc-local"
    if mlflow.get_experiment_by_name(experiment_name) is None:
        mlflow.create_experiment(experiment_name, artifact_location=artifact_dir.resolve().as_uri())
    mlflow.set_experiment(experiment_name)
    tags = {"repository": str(root)}
    if idempotency_key:
        import hashlib
        key = hashlib.sha256(idempotency_key.encode()).hexdigest()
        tags["letsaigc.logical_run"] = key
        experiment = mlflow.get_experiment_by_name(experiment_name)
        matches = mlflow.search_runs(
            experiment_ids=[experiment.experiment_id],
            filter_string=f"tags.`letsaigc.logical_run` = '{key}'",
            output_format="list",
        )
        if len(matches) > 1:
            raise RuntimeError("Duplicate logical MLflow runs require reconciliation")
        if matches:
            existing_run_id = matches[0].info.run_id
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
