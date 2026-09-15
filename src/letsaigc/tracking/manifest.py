from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import psutil

from ..paths import find_repo_root, local_path
from ..policy.gates import sha256_file
from ..schemas import AgentRunMetadata, LicenseLane, MediaMetadata, RunGovernance, RunManifest, RunOutput


def _git(command: list[str]) -> str | None:
    completed = subprocess.run(
        ["git", *command],
        cwd=find_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def environment_snapshot() -> dict[str, Any]:
    return {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "machine": platform.machine(),
        "processor": platform.processor(),
        "ram_bytes": psutil.virtual_memory().total,
        "conda_environment": os.getenv("CONDA_DEFAULT_ENV"),
    }


def create_manifest(
    *,
    kind: str,
    parameters: dict[str, Any],
    license_lanes: list[LicenseLane],
    source: dict[str, Any] | None = None,
    parent_run_id: str | None = None,
    agent: AgentRunMetadata | None = None,
) -> RunManifest:
    git_commit = _git(["rev-parse", "HEAD"])
    git_status = _git(["status", "--short"])
    merged_source = {
        "git_commit": git_commit,
        "git_dirty": bool(git_status),
        "git_status_sha256": hashlib.sha256((git_status or "").encode()).hexdigest(),
        **(source or {}),
    }
    return RunManifest(
        run_id=f"{kind}-{uuid.uuid4().hex[:16]}",
        kind=kind,
        status="created",
        source=merged_source,
        parameters=parameters,
        environment=environment_snapshot(),
        parent_run_id=parent_run_id,
        agent=agent,
        governance=RunGovernance(
            license_lanes=license_lanes,
            validations={"contract": False, "hashes": False, "provenance": True},
        ),
    )


def manifest_path(run_id: str) -> Path:
    return local_path("runs", run_id, "manifest.json")


def save_manifest(manifest: RunManifest) -> Path:
    path = manifest_path(manifest.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_manifest(run_id: str) -> RunManifest:
    return RunManifest.model_validate_json(manifest_path(run_id).read_text(encoding="utf-8"))


def add_output(
    manifest: RunManifest,
    path: Path,
    *,
    role: str | None = None,
    media_kind: str | None = None,
    media: MediaMetadata | None = None,
    derived_from_run_id: str | None = None,
    derived_from_sha256: str | None = None,
) -> None:
    manifest.outputs.append(
        RunOutput(
            path=str(path.resolve()),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            role=role,
            media_kind=media_kind,
            media=media,
            derived_from_run_id=derived_from_run_id,
            derived_from_sha256=derived_from_sha256,
        )
    )


def file_sha256_or_none(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def ui_lineage(service, task_id):
    """Read registered ownership; projections cannot invent parent relationships."""
    with service.ledger.transaction() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] < 5:
            return None
        row = db.execute(
            "SELECT parent_task_id,root_task_id,purpose,source_ids FROM ui_child_bindings WHERE task_id=?",
            (task_id,),
        ).fetchone()
    if row is None:
        return None
    return {**dict(row), "source_ids": json.loads(row["source_ids"])}


def ui_descendant_scopes(service, task_id):
    """An image's registered subtree, excluding its siblings and batch owner."""
    with service.ledger.transaction() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] < 5:
            return {task_id}
        rows = db.execute(
            "WITH RECURSIVE descendants(task_id) AS (SELECT ? UNION "
            "SELECT b.task_id FROM ui_child_bindings b JOIN descendants d ON b.parent_task_id=d.task_id) "
            "SELECT task_id FROM descendants", (task_id,),
        ).fetchall()
    return {row[0] for row in rows}


def create_ui_manifest(service, plan, outputs, *, evidence_kind: str = "runtime"):
    """UI evidence projection uses references; it never embeds source or model bodies."""
    from ..pipelines.errors import PipelineError
    from ..schemas.pipeline import ArtifactRef, canonical_json, validate_payload

    if evidence_kind not in {"offline", "runtime"}:
        raise PipelineError("invalid_evidence_kind")
    refs = [ArtifactRef.model_validate(ref.model_dump(mode="json")) for ref in outputs]
    for ref in refs:
        if ref.task_id != plan.task_id:
            raise PipelineError("artifact_scope")
        service.artifacts.read(ref)
    operations = [operation.model_dump(mode="json") for operation in service.ledger.list_operations(plan.task_id)]
    operations_ref = service.artifacts.put(
        plan.task_id, "project", canonical_json(operations).encode(), role="operation_index"
    )
    manifest = {
        "schema_version": 1, "kind": "ui_analysis", "task_id": plan.task_id,
        "plan_fingerprint": plan.fingerprint, "workflow_type": plan.workflow_type,
        "workflow_version": plan.workflow_version, "quality_status": "pending", "evidence_kind": evidence_kind,
        "git_commit": _git(["rev-parse", "HEAD"]), "python": sys.version.split()[0],
        "inputs": [ref.model_dump(mode="json") for ref in plan.inputs],
        "policies": plan.parameters, "outputs": [ref.model_dump(mode="json") for ref in refs],
        "budget": plan.envelope.budget.model_dump(mode="json"),
        "usage": {name: value.model_dump(mode="json") for name, value in service.ledger.usage(plan.task_id).items()},
        "operations_ref": operations_ref.model_dump(mode="json"),
        "production_export_approved": False,
    }
    lineage = ui_lineage(service, plan.task_id)
    if lineage:
        manifest["lineage"] = lineage
    validate_payload(manifest)
    return service.artifacts.put(plan.task_id, "project", canonical_json(manifest).encode(), role="manifest")


def create_ui_batch_manifest(service, plan, snapshot, children):
    """A bounded source-to-child index; referenced image outputs stay immutable."""
    from ..pipelines.errors import PipelineError
    from ..schemas.pipeline import ArtifactRef, canonical_json, validate_payload

    linked_children = []
    for child in children:
        lineage = ui_lineage(service, child["task_id"])
        if not lineage or lineage["parent_task_id"] != plan.task_id or lineage["purpose"] != "analysis":
            raise PipelineError("artifact_scope")
        scopes = ui_descendant_scopes(service, child["task_id"])
        linked = dict(child)
        for output in child["artifacts"]:
            index = ArtifactRef.model_validate(output)
            if index.role != "child_outputs" or index.task_id != plan.task_id:
                raise PipelineError("artifact_scope")
            for item in json.loads(service.artifacts.read(index)):
                ref = ArtifactRef.model_validate(item)
                if ref.task_id not in scopes:
                    raise PipelineError("artifact_scope")
                service.artifacts.read(ref)
                if ref.role == "manifest" and ref.task_id == child["task_id"]:
                    linked["manifest_ref"] = ref.model_dump(mode="json")
        linked_children.append(linked)

    value = {
        "schema_version": 1, "kind": "ui_batch", "task_id": plan.task_id,
        "root_task_id": plan.task_id,
        "plan_fingerprint": plan.fingerprint, "state": snapshot.state,
        "quality_status": "pending", "production_export_approved": False,
        "entries": snapshot.entries, "children": linked_children,
        "inputs": [ref.model_dump(mode="json") for ref in plan.inputs],
        "budget": plan.envelope.budget.model_dump(mode="json"),
        "usage": {key: amount.model_dump(mode="json") for key, amount in
                  service.ledger.usage(plan.task_id, include_children=True).items()},
    }
    from ..schemas.ui import UIAnalysisRequest
    from ..ui_analysis.execution import UIExecution
    request = UIAnalysisRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(plan.parameters["request_ref"])))
    provision = UIExecution(service).ref(plan, request.input.kind, "sources")
    value["provision_ref"] = provision.model_dump(mode="json")
    validate_payload(value)
    return service.artifacts.put(plan.task_id, "batch-project", canonical_json(value).encode(), role="batch_manifest")
