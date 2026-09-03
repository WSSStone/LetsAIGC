from concurrent.futures import ThreadPoolExecutor

import pytest

from letsaigc.execution.temporal.projection import project
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import PipelineRun, PipelineState


def test_pipeline_mlflow_projection_is_idempotent(monkeypatch, tmp_path):
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("LETSAIGC_ROOT", str(tmp_path))
    monkeypatch.delenv("MLFLOW_URI", raising=False)
    monkeypatch.delenv("MLFLOW_ARTIFACT_ROOT", raising=False)
    service = PipelineService(tmp_path / "pipelines", mlflow_enabled=True)
    plan = service.smoke_plan("tracking")
    run = PipelineRun(
        task_id=plan.task_id,
        workflow_id=plan.task_id,
        temporal_run_id="run-1",
        plan_fingerprint=plan.fingerprint,
        state=PipelineState.succeeded,
        projection_sequence=1,
    )
    # Parallel delivery is serialized by the business ledger, including registration.
    with ThreadPoolExecutor(2) as executor:
        list(executor.map(lambda _: project(service, run), range(2)))
    from letsaigc.tracking.mlflow_store import tracking_uri

    client = mlflow.MlflowClient(tracking_uri=tracking_uri())
    experiment = client.get_experiment_by_name("letsaigc-local")
    assert len(client.search_runs([experiment.experiment_id])) == 1
    # Recover the remote-registration/local-record crash window through its logical tag.
    path = service.root / "tasks" / plan.task_id / "manifest.json"
    path.unlink()
    project(service, run)
    assert len(client.search_runs([experiment.experiment_id])) == 1
