from __future__ import annotations

from pathlib import Path

import mlflow

from letsaigc.tracking.mlflow_store import log_manifest, tracking_uri


def test_local_mlflow_sqlite_is_queryable(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("LETSAIGC_ROOT", str(root))
    monkeypatch.delenv("LETSAIGC_MLFLOW_URI", raising=False)
    run_id = log_manifest("integration-smoke", {"profile": "test"}, {"completed": 1.0})
    assert run_id
    client = mlflow.MlflowClient(tracking_uri=tracking_uri())
    run = client.get_run(run_id)
    assert run.data.params["profile"] == "test"
    assert run.data.metrics["completed"] == 1.0
