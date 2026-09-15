"""T033 parent/child evidence and local MLflow lineage, without model or network calls."""

import json
from pathlib import Path

import pytest

from letsaigc.execution.temporal.projection import project
from letsaigc.schemas import RunManifest
from letsaigc.schemas.pipeline import ArtifactRef, Cost, PipelineRun
from letsaigc.schemas.ui import UIObservation


def edited_family(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "integration"))
    from test_ui_batch import family, run_child
    from test_ui_batch_editing_budget import reserve, segment
    from test_ui_child_approvals import authorize

    service, root, batch = family(tmp_path, request_overrides={
        "output_mode": "decompose", "selection_mode": "deferred",
    })
    outputs = []
    for number in range(2):
        image = batch.prepare(root).child
        image_run = run_child(service, image)
        parent_id = image.task_id
        descendant_outputs = []
        for depth in range(2):
            child = segment(service, image, f"gpu-{number}-{depth}", parent_task_id=parent_id)
            authorize(service, child)
            operation = reserve(service, child)
            output = service.artifacts.put(child.task_id, operation.operation_id, b"offline-mask", role="mask")
            service.ledger.finish(operation.operation_id, Cost(), {"artifacts": [output.model_dump(mode="json")]})
            descendant_outputs.append(output)
            parent_id = child.task_id
        image_run = image_run.model_copy(update={
            "artifacts": [*image_run.artifacts, *descendant_outputs], "projection_sequence": 2,
        })
        project(service, image_run)
        batch.complete(root, image_run)
        outputs.append(descendant_outputs)
    return service, root, batch, outputs


def test_batch_manifest_preserves_recursive_editing_descendant_outputs(tmp_path, monkeypatch):
    service, root, batch, outputs = edited_family(tmp_path, monkeypatch)
    manifest = json.loads(service.artifacts.read(ArtifactRef.model_validate(batch.manifest(root))))
    for child, expected in zip(manifest["children"], outputs, strict=True):
        index = ArtifactRef.model_validate(child["artifacts"][0])
        actual = [ArtifactRef.model_validate(item) for item in json.loads(service.artifacts.read(index))]
        assert all(ref in actual for ref in expected)
        assert child["manifest_ref"]["task_id"] == child["task_id"]


@pytest.mark.parametrize("foreign_scope", ["sibling", "sibling_descendant", "batch"])
def test_batch_manifest_rejects_outputs_outside_each_image_descendants(tmp_path, monkeypatch, foreign_scope):
    from letsaigc.pipelines.errors import PipelineError
    from letsaigc.schemas.pipeline import canonical_json
    from letsaigc.tracking.manifest import create_ui_batch_manifest

    service, root, batch, outputs = edited_family(tmp_path, monkeypatch)
    children = batch.children(root)
    task_id = {"sibling": children[1]["task_id"], "sibling_descendant": outputs[1][-1].task_id,
               "batch": root.task_id}[foreign_scope]
    foreign = service.artifacts.put(task_id, "foreign-output", b"foreign", role="mask")
    index = service.artifacts.put(root.task_id, "invalid-index", canonical_json([
        foreign.model_dump(mode="json")]).encode(), role="child_outputs")
    children[0]["artifacts"] = [index.model_dump(mode="json")]
    with pytest.raises(PipelineError) as caught:
        create_ui_batch_manifest(service, root, batch.status(root), children)
    assert caught.value.code == "artifact_scope"


def test_cancelled_batch_preserves_settled_descendant_before_image_projection(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "integration"))
    from test_ui_batch import family, run_child
    from test_ui_batch_editing_budget import reserve, segment
    from test_ui_child_approvals import authorize

    service, root, batch = family(tmp_path, request_overrides={
        "output_mode": "decompose", "selection_mode": "deferred",
    })
    image = batch.prepare(root).child
    run_child(service, image)
    child = segment(service, image, "settled-before-cancel")
    authorize(service, child)
    operation = reserve(service, child)
    output = service.artifacts.put(child.task_id, operation.operation_id, b"settled-mask", role="mask")
    service.ledger.finish(operation.operation_id, Cost(), {"artifacts": [output.model_dump(mode="json")]})
    assert batch.cancel(root)
    children = batch.children(root)
    index = ArtifactRef.model_validate(children[0]["artifacts"][0])
    refs = [ArtifactRef.model_validate(item) for item in json.loads(service.artifacts.read(index))]
    assert output in refs
    manifest = json.loads(service.artifacts.read(ArtifactRef.model_validate(batch.manifest(root))))
    assert manifest["state"] == "cancelled"
    assert manifest["children"][0]["artifacts"][0] == index.model_dump(mode="json")


def completed_family(tmp_path, monkeypatch, *, tracking=False):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "integration"))
    from test_ui_batch import family, run_child

    service, plan, batch = family(tmp_path)
    service.mlflow_enabled = tracking
    for capability in ("ui.ocr", "ui.analyze"):
        monkeypatch.setattr(service.backends[capability], "inspect",
                            lambda submission: UIObservation(state="succeeded", actual=Cost(cost_usd=0.01)))
    child_runs = []
    for _ in range(2):
        child = batch.prepare(plan).child
        run = run_child(service, child)
        batch.complete(plan, run)
        child_runs.append(run)
    parent_run = PipelineRun(
        task_id=plan.task_id, workflow_id=plan.task_id, temporal_run_id="offline-parent",
        plan_fingerprint=plan.fingerprint, state="succeeded", projection_sequence=1,
        artifacts=[ArtifactRef.model_validate(batch.manifest(plan))],
    )
    return service, plan, parent_run, child_runs


def test_immutable_batch_and_child_manifests_link_to_ledger_lineage(tmp_path, monkeypatch):
    service, plan, parent_run, child_runs = completed_family(tmp_path, monkeypatch)
    project(service, parent_run)
    parent = json.loads(service.artifacts.read(parent_run.artifacts[0]))
    assert parent["root_task_id"] == plan.task_id
    for item, run in zip(parent["children"], child_runs, strict=True):
        ref = ArtifactRef.model_validate(item["manifest_ref"])
        assert ref.task_id == run.task_id and ref.role == "manifest"
        child = json.loads(service.artifacts.read(ref))
        assert child["lineage"] == {
            "root_task_id": plan.task_id, "parent_task_id": plan.task_id,
            "purpose": "analysis", "source_ids": item["source_ids"],
        }
        assert child["quality_status"] == "pending"
        local = RunManifest.model_validate_json(
            (service.root / "tasks" / run.task_id / "manifest.json").read_text())
        parent_local = RunManifest.model_validate_json(
            (service.root / "tasks" / plan.task_id / "manifest.json").read_text())
        assert local.parent_run_id == parent_local.run_id
        assert local.tracking["temporal"]["lineage"] == child["lineage"]


def test_mlflow_child_before_parent_and_replay_keep_one_correct_run_family(tmp_path, monkeypatch):
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("LETSAIGC_ROOT", str(tmp_path))
    monkeypatch.setenv("MLFLOW_URI", f"sqlite:///{tmp_path / 'tracking.db'}")
    monkeypatch.setenv("MLFLOW_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    service, plan, parent_run, child_runs = completed_family(tmp_path, monkeypatch, tracking=True)
    client = mlflow.MlflowClient(tracking_uri=f"sqlite:///{tmp_path / 'tracking.db'}")
    experiment = client.get_experiment_by_name("letsaigc-local")
    runs = client.search_runs([experiment.experiment_id])
    # Children complete before the parent's terminal projection. Their parent
    # must already exist with its own stable logical identity.
    assert len(runs) == 3
    by_task = {run.data.params["task_id"]: run for run in runs}
    parent_id = by_task[plan.task_id].info.run_id
    assert all(by_task[run.task_id].data.tags["mlflow.parentRunId"] == parent_id for run in child_runs)
    project(service, parent_run)
    for run in [*child_runs, parent_run]:
        (service.root / "tasks" / run.task_id / "manifest.json").unlink()
        project(service, run)
        project(service, run)
    runs = client.search_runs([experiment.experiment_id])
    assert len(runs) == 3
    parent = next(run for run in runs if run.data.params["task_id"] == plan.task_id)
    assert parent.info.run_id == parent_id
    assert parent.data.metrics["cost_usd"] == pytest.approx(0.04)
    for run in child_runs:
        local = RunManifest.model_validate_json(
            (service.root / "tasks" / run.task_id / "manifest.json").read_text())
        assert client.get_run(local.tracking["mlflow_run_id"]).data.tags["mlflow.parentRunId"] == parent_id


def test_recovered_existing_mlflow_run_gets_missing_parent_tag(tmp_path, monkeypatch):
    mlflow = pytest.importorskip("mlflow")
    from letsaigc.tracking.mlflow_store import log_manifest

    monkeypatch.setenv("LETSAIGC_ROOT", str(tmp_path))
    monkeypatch.setenv("MLFLOW_URI", f"sqlite:///{tmp_path / 'tracking.db'}")
    monkeypatch.setenv("MLFLOW_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    parent_id = log_manifest("parent", {}, {}, idempotency_key="parent")
    child_id = log_manifest("child", {}, {}, idempotency_key="child")
    recovered = log_manifest("child", {}, {}, parent_run_id=parent_id, idempotency_key="child")
    assert recovered == child_id
    client = mlflow.MlflowClient(tracking_uri=f"sqlite:///{tmp_path / 'tracking.db'}")
    assert client.get_run(child_id).data.tags["mlflow.parentRunId"] == parent_id
