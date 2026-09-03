"""Rebuildable local evidence; never used as workflow scheduling state."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from ...files import replace_file
from ...pipelines.errors import PipelineError
from ...pipelines.service import PipelineService
from ...schemas import RunManifest
from ...schemas.pipeline import PipelineRun, canonical_json, digest, validate_payload
from ...tracking.manifest import add_output, create_manifest


def atomic_json(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def project(service: PipelineService, run: PipelineRun) -> None:
    validate_payload(run)
    plan = service.checked_plan(run.task_id, run.plan_fingerprint, verify_inputs=False)
    path = service.root / "tasks" / run.task_id / "manifest.json"
    # Also serializes MLflow registration among local workers. Crash after the remote
    # registration is recovered by its stable logical-run tag, before scheduling resumes.
    with service.ledger.transaction() as db:
        previous = db.execute("SELECT * FROM projections WHERE task_id=?", (run.task_id,)).fetchone()
        if previous and previous["sequence"] > run.projection_sequence:
            return
        if previous and previous["sequence"] == run.projection_sequence:
            if previous["payload"] != canonical_json(run):
                raise PipelineError("projection_conflict")
        if path.exists():
            manifest = RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
        else:
            manifest = create_manifest(
                kind="agent_task",
                parameters={"plan_fingerprint": plan.fingerprint},
                license_lanes=[],
                source={"pipeline_plan": plan.model_dump(mode="json")},
            )
            manifest.run_id = "pipeline-" + digest(plan.task_id)[:32]
        manifest.status = {
            "succeeded": "succeeded",
            "failed": "failed",
            "cancelled": "cancelled",
            "rejected": "cancelled",
            "planned": "created",
            "awaiting_approval": "validated",
        }.get(run.state.value, "running")
        manifest.outputs = []
        for ref in run.artifacts:
            service.artifacts.read(ref)
            add_output(manifest, service.artifacts.resolve(ref), role=ref.role)
        operations = [
            service.ledger._record(row)
            for row in db.execute(
                "SELECT * FROM operations WHERE task_id=? ORDER BY revision",
                (run.task_id,),
            ).fetchall()
        ]
        manifest.tracking["temporal"] = {
            "task_id": run.task_id,
            "workflow_id": run.workflow_id,
            "run_id": run.temporal_run_id,
            "workflow_version": run.workflow_version,
            "activity_version": "1",
            "plan_fingerprint": plan.fingerprint,
            "state": run.state.value,
            "operations": [operation.model_dump(mode="json") for operation in operations],
        }
        manifest.governance.validations.update(contract=True, hashes=True, provenance=True)
        if service.mlflow_enabled and run.state.value in {"succeeded", "failed", "cancelled", "rejected"}:
            from ...tracking.mlflow_store import log_manifest

            atomic_json(path, manifest.model_dump_json(indent=2))
            tracking_id = log_manifest(
                manifest.run_id,
                {"task_id": run.task_id, "plan_fingerprint": plan.fingerprint},
                {
                    "cost_usd": sum(op.actual.cost_usd for op in operations),
                    "gpu_minutes": sum(op.actual.gpu_minutes for op in operations),
                },
                artifacts=[path],
                idempotency_key=manifest.run_id,
            )
            if tracking_id:
                manifest.tracking["mlflow_run_id"] = tracking_id
        atomic_json(path, manifest.model_dump_json(indent=2))
        atomic_json(path.with_name("run.json"), run.model_dump_json(indent=2))
        db.execute(
            """INSERT INTO projections(task_id,sequence,payload) VALUES(?,?,?)
            ON CONFLICT(task_id) DO UPDATE SET sequence=excluded.sequence,payload=excluded.payload""",
            (run.task_id, run.projection_sequence, canonical_json(run)),
        )


def local_projection(service: PipelineService, task_id: str) -> dict | None:
    with service.ledger.transaction() as db:
        row = db.execute("SELECT payload FROM projections WHERE task_id=?", (task_id,)).fetchone()
    return json.loads(row["payload"]) if row else None
