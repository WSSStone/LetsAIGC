"""Root-owned sequential image preparation on the shared v5 ledger."""

import json
from io import BytesIO
from typing import Any

from PIL import Image
from pydantic import Field

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ApprovalEnvelope, ArtifactRef, PipelineModel, PipelinePlan, canonical_json, digest
from ..schemas.ui import UIEvaluationPolicy, UIPolicy, UISelection
from ..schemas.ui_provider import ManualUIInput, UIInputEntry, UIInputManifest, UIProvisionResult
from .execution import LocalUIBackend, UIExecution


class BatchPreparation(PipelineModel):
    state: str
    child: PipelinePlan | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=4)
    reason: str | None = None
    editing: bool = False
    active_limit_seconds: int = Field(default=3600, ge=1, le=3600, strict=True)


class BatchExecution:
    def __init__(self, service):
        self.service = service
        self.ledger = service.ledger
        self.store = service.artifacts
        self.execution = UIExecution(service)

    def _check(self, plan):
        if plan.workflow_type != "ui_batch":
            raise PipelineError("invalid_plan")
        self.service.checked_plan(plan.task_id, plan.fingerprint)
        with self.ledger.transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] < 5:
                raise PipelineError("migration_required", "Batch execution requires an offline v5 migration")
        return self.execution.request(plan)

    def _provision(self, plan):
        request = self.execution.request(plan)
        try:
            return UIProvisionResult.model_validate_json(
                self.store.read(self.execution.ref(plan, request.input.kind, "sources"))
            )
        except PipelineError as exc:
            if exc.code != "unknown_operation":
                raise
        if request.input.kind == "search" and isinstance(self.service.backends["ui.search"], LocalUIBackend):
            from ..ui_providers.search import SearchAcquisition

            SearchAcquisition(self.service).acquire(plan)
        prepared = self.execution.prepare(plan, "provide", 0)
        op = self.service.submit_step(plan, prepared.binding, prepared.reservation)
        if op.state not in {"succeeded", "failed"}:
            self.service.observe_step(plan, op.operation_id)
            op = self.service.collect_step(plan, op.operation_id)
        if op.state != "succeeded":
            raise PipelineError("input_resupply_required")
        return UIProvisionResult.model_validate_json(
            self.store.read(self.execution.ref(plan, request.input.kind, "sources"))
        )

    def _manifest(self, plan, provision):
        if provision.provider_id == "manual":
            return UIInputManifest.model_validate_json(self.store.read(provision.index_ref))
        return UIInputManifest(
            task_id=plan.task_id,
            status="ready" if provision.sources else "unavailable",
            sources=provision.sources,
            entries=[
                UIInputEntry(entry_id=source.input_entry_ids[0], status="ready", source_id=source.source_id)
                for source in provision.sources
            ],
        )

    def _children(self, plan):
        with self.ledger.transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM ui_child_bindings WHERE parent_task_id=? AND purpose='analysis' ORDER BY rowid",
                    (plan.task_id,),
                ).fetchall()
            ]

    def _view(self, plan, provision):
        manifest = self._manifest(plan, provision)
        children = self._children(plan)
        by_source = {json.loads(row["source_ids"])[0]: row for row in children}
        by_id = {source.source_id: source for source in manifest.sources}
        first_sha, entries, phashes = {}, [], []
        active = None
        from ..ui_providers.search import perceptual_hash

        for entry in manifest.entries:
            value = entry.model_dump(mode="json")
            value.update(
                child_task_id=None, duplicate_of=None, status="failed" if entry.status == "failed" else "unprocessed"
            )
            source = by_id.get(entry.source_id)
            if source is not None:
                original = first_sha.setdefault(source.original_ref.sha256, source)
                if original.source_id != source.source_id:
                    value["duplicate_of"] = original.input_entry_ids[0]
                else:
                    with Image.open(BytesIO(self.store.read(source.original_ref))) as image:
                        phash = perceptual_hash(image)
                    value["near_duplicate_of"] = next(
                        (previous for previous, hash_ in phashes if (int(hash_, 16) ^ int(phash, 16)).bit_count() <= 6),
                        None,
                    )
                    phashes.append((entry.entry_id, phash))
                row = by_source.get(original.source_id)
                if row:
                    value["child_task_id"] = row["task_id"]
                    value["status"] = row["status"].split(":", 1)[0]
                    if ":" in row["status"]:
                        value["error_code"] = row["status"].split(":", 1)[1]
                    if row["active"]:
                        active = self.ledger.plan(row["task_id"])
                        value["status"] = "running"
                        with self.ledger.transaction() as db:
                            projection = db.execute(
                                "SELECT payload FROM projections WHERE task_id=?", (row["task_id"],)
                            ).fetchone()
                        if projection:
                            state = json.loads(projection[0])["state"]
                            if state in {"awaiting_approval", "awaiting_reconciliation"}:
                                value["status"] = state
            entries.append(value)
        state = (
            next((item["status"] for item in entries if item["child_task_id"] == active.task_id), "running")
            if active
            else "ready"
        )
        if entries and all(item["status"] in {"succeeded", "failed", "cancelled", "rejected"} for item in entries):
            state = (
                "succeeded"
                if provision.status != "partial" and all(item["status"] == "succeeded" for item in entries)
                else "partial"
            )
        reason = None
        if active is None and self.execution.request(plan).batch_failure_policy == "stop_on_error":
            for item in entries:
                if item["status"] == "unprocessed":
                    break
                if item["status"] == "failed":
                    state, reason = "partial", item.get("error_code") or "input_failed"
                    break
        return BatchPreparation(
            state=state,
            child=active,
            entries=entries,
            reason=reason,
            editing=self.execution.request(plan).output_mode != "parse",
            active_limit_seconds=self.execution.request(plan).resources.active_seconds,
        ), manifest

    def status(self, plan):
        request = self._check(plan)
        try:
            provision = UIProvisionResult.model_validate_json(
                self.store.read(self.execution.ref(plan, request.input.kind, "sources"))
            )
        except PipelineError as exc:
            if exc.code not in {"unknown_operation", "dependency_not_ready"}:
                raise
            provision = None
        view = self._view(plan, provision)[0] if provision else BatchPreparation(state="planned")
        if provision is None:
            operations = self.ledger.list_operations(plan.task_id)
            if any(op.state not in {"succeeded", "failed"} for op in operations):
                view = view.model_copy(update={"state": "awaiting_reconciliation", "reason": "outcome_unknown"})
            elif any(op.state == "failed" and op.step_id == request.input.kind for op in operations):
                view = view.model_copy(update={"state": "failed", "reason": "input_resupply_required"})
        with self.ledger.transaction() as db:
            row = db.execute(
                "SELECT submission_gate,stop_reason FROM ui_budget_groups WHERE root_task_id=?", (plan.task_id,)
            ).fetchone()
        if row and row["submission_gate"] != "open":
            state = "cancel_requested" if row["stop_reason"] == "cancelled" else "failed"
            with self.ledger.transaction() as db:
                unresolved = db.execute(
                    "SELECT 1 FROM operations WHERE state NOT IN ('succeeded','failed') AND "
                    "(task_id=? OR task_id IN (SELECT task_id FROM ui_child_bindings WHERE root_task_id=?))",
                    (plan.task_id, plan.task_id),
                ).fetchone()
            if unresolved:
                state = "awaiting_reconciliation"
            elif not view.child and row["stop_reason"] == "cancelled":
                state = "cancelled"
            view = view.model_copy(update={"state": state, "reason": row["stop_reason"]})
        else:
            with self.ledger.transaction() as db:
                projection = db.execute("SELECT payload FROM projections WHERE task_id=?", (plan.task_id,)).fetchone()
            if projection:
                payload = json.loads(projection[0])
                if payload["state"] == "failed":
                    view = view.model_copy(update={"state": "failed", "reason": payload.get("stop_reason")})
        return view

    def prepare(self, plan):
        request = self._check(plan)
        current = self.status(plan)
        if (
            current.state in {"cancelled", "cancel_requested", "awaiting_reconciliation", "failed"}
            or current.child is not None
        ):
            return current
        with self.ledger.transaction() as db:
            task = db.execute("SELECT approved,cancelled FROM tasks WHERE task_id=?", (plan.task_id,)).fetchone()
            if task["cancelled"]:
                raise PipelineError("cancelled")
            if not task["approved"]:
                raise PipelineError("approval_required")
            self.ledger._ui_gate(db, plan.task_id)
        provision = self._provision(plan)
        view, manifest = self._view(plan, provision)
        if view.child or view.state in {"succeeded", "partial"}:
            return view
        for item in view.entries:
            if item["status"] == "failed" and request.batch_failure_policy == "stop_on_error":
                return view.model_copy(update={"state": "partial", "reason": "input_failed"})
            if item["status"] == "unprocessed" and item.get("duplicate_of") is None:
                source = next(source for source in manifest.sources if source.source_id == item["source_id"])
                child = self._child(plan, request, source)
                view, _ = self._view(plan, provision)
                return view.model_copy(update={"child": child, "state": "running"})
        return view

    def _copy(self, ref, task_id, scope):
        if ref.task_id != scope:
            raise PipelineError("artifact_scope")
        data = self.store.read(ref)
        if ref.media_type == "application/json":
            try:
                value = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                value = None
            if value is not None:

                def rewrite(item):
                    if isinstance(item, dict):
                        if {"task_id", "artifact_id", "sha256", "key", "role"} <= item.keys():
                            return self._copy(ArtifactRef.model_validate(item), task_id, scope).model_dump(mode="json")
                        return {key: rewrite(value) for key, value in item.items()}
                    if isinstance(item, list):
                        return [rewrite(value) for value in item]
                    return item

                data = canonical_json(rewrite(value)).encode()
        return self.store.put(
            task_id, "batch-input", data, role=ref.role, media_type=ref.media_type, source_ids=[ref.artifact_id]
        )

    def _child(self, plan, request, source):
        task_id = "ui-image-" + digest([plan.fingerprint, source.original_ref.sha256])[:32]
        original = self._copy(source.original_ref, task_id, plan.task_id)
        provenance = self._copy(source.provenance_ref, task_id, plan.task_id)
        manifest = UIInputManifest(
            task_id=task_id,
            status="ready",
            entries=[UIInputEntry(entry_id=source.input_entry_ids[0], source_id=source.source_id, status="ready")],
            sources=[source.model_copy(update={"original_ref": original, "provenance_ref": provenance})],
        )
        manifest_ref = self.store.put(
            task_id,
            "batch-input",
            canonical_json(manifest).encode(),
            role="input_manifest",
            source_ids=[source.original_ref.artifact_id, source.provenance_ref.artifact_id],
        )
        selection_ref = None
        if request.selection_ref:
            selection = UISelection.model_validate_json(self.store.read(request.selection_ref))
            selected = [
                item
                for item in selection.sources
                if item.source_id == source.source_id and item.original_sha256 == source.original_ref.sha256
            ]
            if len(selected) != 1:
                raise PipelineError("source_scope")
            selected_ref = self.store.put(
                plan.task_id,
                "batch-selection",
                canonical_json(UISelection(sources=selected)).encode(),
                role="selection",
                source_ids=[request.selection_ref.artifact_id],
            )
            selection_ref = self._copy(selected_ref, task_id, plan.task_id)
        child_request = request.model_copy(
            update={
                "input": ManualUIInput(inputs=[original], metadata_ref=manifest_ref),
                "model_bindings": {
                    key: self._copy(ref, task_id, plan.task_id) for key, ref in request.model_bindings.items()
                },
                "selection_ref": selection_ref,
            }
        )
        params = {
            name + "_ref": self.store.put(task_id, "plan", canonical_json(value).encode(), role=name).model_dump(
                mode="json"
            )
            for name, value in {
                "request": child_request,
                "policy": UIPolicy(resources=request.resources, limits=request.limits),
                "evaluation": UIEvaluationPolicy(),
            }.items()
        }
        child = PipelinePlan(
            task_id=task_id,
            workflow_type="ui_analysis",
            inputs=child_request.references(),
            parameters=params,
            envelope=ApprovalEnvelope(
                stage="analysis",
                allowed_capabilities=[
                    "ui.manual",
                    "ui.normalize",
                    "ui.ocr",
                    "ui.analyze",
                    "ui.layout",
                    "ui.crop",
                    "ui.project",
                ],
                budget=request.budget,
            ),
        )
        from ..pipelines.registry import validate_ui_registration

        validate_ui_registration(child, self.store)
        with self.ledger.transaction() as db:
            self.ledger._ui_gate(db, plan.task_id)
            task = db.execute("SELECT approved,cancelled FROM tasks WHERE task_id=?", (plan.task_id,)).fetchone()
            if not task["approved"] or task["cancelled"]:
                raise PipelineError("cancelled" if task["cancelled"] else "approval_required")
            old = db.execute("SELECT fingerprint FROM ui_child_bindings WHERE task_id=?", (task_id,)).fetchone()
            if old:
                if old[0] != child.fingerprint:
                    raise PipelineError("task_conflict")
                return child
            if db.execute(
                "SELECT 1 FROM ui_child_bindings WHERE root_task_id=? AND purpose='analysis' AND active=1",
                (plan.task_id,),
            ).fetchone():
                raise PipelineError("active_child")
            # Inherit only the already approved analysis scope. No approval receipt
            # is fabricated; GPU children still require their own exact approval.
            db.execute(
                "INSERT INTO tasks(task_id,fingerprint,plan,approved) VALUES(?,?,?,1)",
                (task_id, child.fingerprint, canonical_json(child)),
            )
            db.execute(
                """INSERT INTO ui_child_bindings
                (task_id,parent_task_id,root_task_id,purpose,source_ids,request_ref,selection_ref,
                 selection_revision,selection_hash,budget,status,active,fingerprint,source_chain,edit_chain)
                VALUES(?,?,?,'analysis',?,?,?,0,?,?,'running',1,?,?,'[]')""",
                (
                    task_id,
                    plan.task_id,
                    plan.task_id,
                    canonical_json([source.source_id]),
                    canonical_json(params["request_ref"]),
                    canonical_json(manifest_ref),
                    manifest_ref.sha256,
                    canonical_json(request.budget),
                    child.fingerprint,
                    canonical_json([source.original_ref.sha256]),
                ),
            )
        return child

    def complete(self, plan, child_run):
        self._check(plan)
        child = self.ledger.plan(child_run.task_id)
        if child_run.state.value == "failed" and child_run.stop_reason not in {
            "model_failed",
            "provider_failed",
            "invalid_input",
            "input_resupply_required",
            "image_criteria",
        }:
            raise PipelineError(child_run.stop_reason or "invalid_child_result")
        if child.fingerprint != child_run.plan_fingerprint or child_run.state.value not in {
            "succeeded",
            "failed",
            "cancelled",
            "rejected",
        }:
            raise PipelineError("invalid_child_result")
        with self.ledger.transaction() as db:
            binding = db.execute(
                "SELECT * FROM ui_child_bindings WHERE task_id=? AND parent_task_id=? AND purpose='analysis'",
                (child.task_id, plan.task_id),
            ).fetchone()
            if binding is None:
                raise PipelineError("source_scope")
            operations = db.execute(
                "WITH RECURSIVE descendants(task_id) AS (SELECT ? UNION ALL "
                "SELECT b.task_id FROM ui_child_bindings b JOIN descendants d ON b.parent_task_id=d.task_id) "
                "SELECT o.* FROM operations o JOIN descendants d ON o.task_id=d.task_id",
                (child.task_id,),
            ).fetchall()
            if any(row["state"] not in {"succeeded", "failed"} for row in operations):
                raise PipelineError("awaiting_reconciliation")
            projection = db.execute("SELECT payload FROM projections WHERE task_id=?", (child.task_id,)).fetchone()
            if projection is None or json.loads(projection[0])["state"] != child_run.state.value:
                raise PipelineError("invalid_child_result")
            if child_run.state.value == "succeeded" and not any(
                row["step_id"] == "project" and row["state"] == "succeeded" for row in operations
            ):
                raise PipelineError("invalid_child_result")
            status = child_run.state.value
            if status == "failed" and child_run.stop_reason:
                status += ":" + child_run.stop_reason
            if not binding["active"] and binding["status"] != status:
                raise PipelineError("settlement_conflict")
            db.execute("UPDATE ui_child_bindings SET status=?,active=0 WHERE task_id=?", (status, child.task_id))
        return self.status(plan)

    def checkpoint(self, plan):
        self._check(plan)
        if any(row["active"] for row in self._children(plan)):
            raise PipelineError("active_child")
        return self.status(plan)

    def cancel(self, plan):
        self.ledger.request_cancel(plan.task_id)
        confirmed = self.execution.cancel(plan)
        with self.ledger.transaction() as db:
            rows = db.execute("SELECT task_id FROM ui_child_bindings WHERE root_task_id=?", (plan.task_id,)).fetchall()
        for row in rows:
            confirmed = self.execution.cancel(self.ledger.plan(row["task_id"])) and confirmed
        if confirmed:
            with self.ledger.transaction() as db:
                db.execute(
                    "UPDATE ui_child_bindings SET status='cancelled',active=0 "
                    "WHERE root_task_id=? AND purpose='analysis' AND active=1",
                    (plan.task_id,),
                )
        return confirmed

    def children(self, plan):
        from ..tracking.manifest import ui_descendant_scopes

        result = []
        for row in self._children(plan):
            child = self.ledger.plan(row["task_id"])
            refs = self.execution.outputs(child)
            scopes = ui_descendant_scopes(self.service, child.task_id)
            # A settled editing operation can precede its image projection.
            # Preserve that evidence even after cancellation or worker failure.
            for task_id in sorted(scopes - {child.task_id}):
                for operation in self.ledger.list_operations(task_id):
                    if operation.state in {"succeeded", "failed"}:
                        refs.extend(ArtifactRef.model_validate(item) for item in operation.result.get("artifacts", []))
            # Each child gets its own immutable output index; the parent remains
            # bounded even when an image has hundreds of crops and glyphs.
            with self.ledger.transaction() as db:
                projection = db.execute("SELECT payload FROM projections WHERE task_id=?", (child.task_id,)).fetchone()
            if projection:
                refs.extend(ArtifactRef.model_validate(item) for item in json.loads(projection[0])["artifacts"])
            refs = list({ref.key: ref for ref in refs}.values())
            for ref in refs:
                if ref.task_id not in scopes:
                    raise PipelineError("artifact_scope")
                self.store.read(ref)
            index = self.store.put(
                plan.task_id,
                "batch-project",
                canonical_json([ref.model_dump(mode="json") for ref in refs]).encode(),
                role="child_outputs",
                source_ids=[ref.artifact_id for ref in refs],
            )
            result.append(
                {
                    "task_id": child.task_id,
                    "plan_fingerprint": child.fingerprint,
                    "source_ids": json.loads(row["source_ids"]),
                    "status": row["status"].split(":", 1)[0],
                    "artifacts": [index.model_dump(mode="json")],
                }
            )
        return result

    def manifest(self, plan):
        from ..tracking.manifest import create_ui_batch_manifest

        snapshot = self.status(plan)
        return create_ui_batch_manifest(self.service, plan, snapshot, self.children(plan))
