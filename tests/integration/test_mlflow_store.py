from __future__ import annotations

from pathlib import Path

import mlflow

from letsaigc.tracking.mlflow_store import artifact_root, log_manifest, tracking_uri


def test_local_mlflow_sqlite_is_queryable(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("LETSAIGC_ROOT", str(root))
    monkeypatch.delenv("MLFLOW_URI", raising=False)
    monkeypatch.delenv("MLFLOW_ARTIFACT_ROOT", raising=False)
    run_id = log_manifest("integration-smoke", {"profile": "test"}, {"completed": 1.0})
    assert run_id
    client = mlflow.MlflowClient(tracking_uri=tracking_uri())
    run = client.get_run(run_id)
    assert run.data.params["profile"] == "test"
    assert run.data.metrics["completed"] == 1.0


def test_mlflow_env_overrides_uri_and_artifact_root(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("LETSAIGC_ROOT", str(root))
    monkeypatch.setenv("MLFLOW_URI", "sqlite:///custom.db")
    monkeypatch.setenv("MLFLOW_ARTIFACT_ROOT", ".local/custom/artifacts")
    assert tracking_uri() == "sqlite:///custom.db"
    resolved = artifact_root()
    assert resolved == root / ".local/custom/artifacts"
    assert resolved.is_dir()
