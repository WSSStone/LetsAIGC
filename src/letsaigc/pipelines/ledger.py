"""Single-host transactional ledger; retries never imply a new business operation."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

from ..schemas.pipeline import (
    ApprovalRequest,
    Cost,
    OperationRecord,
    PipelinePlan,
    canonical_json,
    operation_id,
    validate_payload,
)
from .errors import PipelineError

SCALE = 1_000_000


def units(value: float, *, limit: bool = False) -> int:
    return int((Decimal(str(value)) * SCALE).to_integral_value(rounding=ROUND_FLOOR if limit else ROUND_CEILING))


class Ledger:
    def __init__(self, path: Path, *, initialize_ui: bool = False) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1, 2, 3}:
                raise PipelineError("ledger_version", "Unsupported ledger schema version")
            db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, plan TEXT NOT NULL,
                approved INTEGER NOT NULL DEFAULT 0, cancelled INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS approvals (
                request_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, payload TEXT NOT NULL,
                actor TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, step_id TEXT NOT NULL,
                revision INTEGER NOT NULL, input_hash TEXT NOT NULL, state TEXT NOT NULL,
                provider_request_id TEXT, reserved_cost INTEGER NOT NULL, reserved_gpu INTEGER NOT NULL,
                actual_cost INTEGER NOT NULL DEFAULT 0, actual_gpu INTEGER NOT NULL DEFAULT 0,
                result TEXT NOT NULL DEFAULT '{}',
                UNIQUE(task_id,step_id,revision,input_hash))""")
            db.execute("""CREATE TABLE IF NOT EXISTS resources (
                resource TEXT PRIMARY KEY, operation_id TEXT NOT NULL, uncertain INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS projections (
                task_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, payload TEXT NOT NULL)""")
            if version == 0:
                db.execute("PRAGMA user_version=1")
                if initialize_ui:
                    from .migrations import _upgrade_v2

                    _upgrade_v2(db)
                    if initialize_ui == 3:
                        from .migrations import _upgrade_v3

                        _upgrade_v3(db)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=30000")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def register(self, plan: PipelinePlan) -> None:
        from .registry import validate_registration

        plan = PipelinePlan.model_validate(plan.model_dump(mode="json"))

        validate_registration(plan)
        with self.transaction() as db:
            old = db.execute("SELECT fingerprint FROM tasks WHERE task_id=?", (plan.task_id,)).fetchone()
            if old and old["fingerprint"] != plan.fingerprint:
                raise PipelineError("task_conflict", "Task ID already belongs to a different immutable plan")
            db.execute(
                "INSERT OR IGNORE INTO tasks(task_id,fingerprint,plan) VALUES(?,?,?)",
                (plan.task_id, plan.fingerprint, canonical_json(plan)),
            )

    def plan(self, task_id: str) -> PipelinePlan:
        with self.transaction() as db:
            row = db.execute("SELECT plan FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise PipelineError("unknown_task", "Unknown pipeline task")
        return PipelinePlan.model_validate_json(row["plan"])

    def record_approval(self, request: ApprovalRequest, *, actor: str) -> None:
        plan = self.plan(request.task_id)
        if request.plan_fingerprint != plan.fingerprint:
            raise PipelineError("approval_mismatch", "Approval fingerprint does not match the stored plan")
        if not actor or len(actor) > 128:
            raise PipelineError("approval_identity", "A local approving identity is required")
        payload = canonical_json(request)
        with self.transaction() as db:
            old = db.execute("SELECT payload FROM approvals WHERE request_id=?", (request.request_id,)).fetchone()
            if old and old["payload"] != payload:
                raise PipelineError("approval_conflict", "Approval request ID has different contents")
            db.execute(
                "INSERT OR IGNORE INTO approvals(request_id,task_id,payload,actor) VALUES(?,?,?,?)",
                (request.request_id, request.task_id, payload, actor),
            )

    def consume_approval(self, request: ApprovalRequest) -> bool:
        with self.transaction() as db:
            row = db.execute("SELECT payload FROM approvals WHERE request_id=?", (request.request_id,)).fetchone()
            if not row or row["payload"] != canonical_json(request):
                raise PipelineError("untrusted_approval", "No matching approval receipt from the local user entrypoint")
            task = db.execute("SELECT fingerprint FROM tasks WHERE task_id=?", (request.task_id,)).fetchone()
            if task is None or task["fingerprint"] != request.plan_fingerprint:
                raise PipelineError("approval_mismatch")
            approved = request.decision == "approve"
            db.execute("UPDATE approvals SET consumed=1 WHERE request_id=?", (request.request_id,))
            db.execute("UPDATE tasks SET approved=? WHERE task_id=?", (int(approved), request.task_id))
        return approved

    def request_cancel(self, task_id: str) -> None:
        with self.transaction() as db:
            db.execute("UPDATE tasks SET cancelled=1 WHERE task_id=?", (task_id,))

    def reserve(
        self,
        plan: PipelinePlan,
        step_id: str,
        revision: int,
        cost: Cost,
        *,
        resource: str | None = None,
        ui_binding=None,
        ui_request=None,
        admission=None,
    ) -> OperationRecord:
        if not 0 <= revision <= plan.envelope.budget.max_revisions:
            raise PipelineError("revision_limit", "Revision exceeds approved scope")
        key = operation_id(plan, step_id, revision)
        reserve_cost, reserve_gpu = units(cost.cost_usd), units(cost.gpu_minutes)
        budget = plan.envelope.budget
        with self.transaction() as db:
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (plan.task_id,)).fetchone()
            if not task or task["fingerprint"] != plan.fingerprint or not task["approved"]:
                raise PipelineError("approval_required", "A consumed exact approval is required")
            if task["cancelled"]:
                raise PipelineError("cancelled", "The pipeline is cancelling")
            if plan.workflow_type == "ui_analysis":
                self._check_ui_binding(db, plan, step_id, revision, ui_binding, ui_request)
            old = db.execute("SELECT * FROM operations WHERE operation_id=?", (key,)).fetchone()
            if old:
                return self._record(old)
            if plan.workflow_type == "ui_analysis":
                self._ui_gate(db, plan.task_id)
            if admission is not None:
                # Trusted local quota admission is part of this same transaction.
                # It cannot perform provider I/O or bypass the common money gate.
                admitted_cost = admission(db, key)
                reserve_cost, reserve_gpu = units(admitted_cost.cost_usd), units(admitted_cost.gpu_minutes)
            if reserve_cost > units(budget.max_iteration_cost_usd, limit=True):
                raise PipelineError("iteration_budget", "Per-iteration monetary budget exceeded")
            if reserve_gpu > units(budget.max_iteration_gpu_minutes, limit=True):
                raise PipelineError("iteration_budget", "Per-iteration GPU budget exceeded")
            spent = db.execute(
                """SELECT COALESCE(SUM(reserved_cost+actual_cost),0) AS cost,
                COALESCE(SUM(reserved_gpu+actual_gpu),0) AS gpu FROM operations WHERE task_id=?""",
                (plan.task_id,),
            ).fetchone()
            if spent["cost"] + reserve_cost > units(budget.max_total_cost_usd, limit=True) or spent[
                "gpu"
            ] + reserve_gpu > units(budget.max_total_gpu_minutes, limit=True):
                raise PipelineError("total_budget", "Total budget, including uncertain usage, would be exceeded")
            if resource:
                owner = db.execute("SELECT operation_id FROM resources WHERE resource=?", (resource,)).fetchone()
                if owner and owner["operation_id"] != key:
                    raise PipelineError("resource_busy", "Resource is owned by another operation; await reconciliation")
                db.execute("INSERT OR IGNORE INTO resources(resource,operation_id) VALUES(?,?)", (resource, key))
            if ui_binding is not None:
                db.execute(
                    "INSERT OR IGNORE INTO ui_step_bindings(task_id,step_id,revision,input_hash,binding) "
                    "VALUES(?,?,?,?,?)",
                    (plan.task_id, step_id, revision, ui_binding.input_hash, canonical_json(ui_binding)),
                )
            db.execute(
                """INSERT INTO operations(
                operation_id,task_id,step_id,revision,input_hash,state,reserved_cost,reserved_gpu)
                VALUES(?,?,?,?,?,'prepared',?,?)""",
                (key, plan.task_id, step_id, revision, plan.fingerprint, reserve_cost, reserve_gpu),
            )
            return self._record(db.execute("SELECT * FROM operations WHERE operation_id=?", (key,)).fetchone())

    def get(self, key: str) -> OperationRecord:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (key,)).fetchone()
        if row is None:
            raise PipelineError("unknown_operation", "Unknown pipeline operation")
        return self._record(row)

    def begin_submit(self, key: str) -> bool:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (key,)).fetchone()
            if row is None:
                raise PipelineError("unknown_operation")
            task = db.execute("SELECT approved,cancelled FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone()
            if not task or not task["approved"] or task["cancelled"]:
                raise PipelineError("cancelled" if task and task["cancelled"] else "approval_required")
            plan = db.execute("SELECT plan FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone()
            if json.loads(plan["plan"])["workflow_type"] == "ui_analysis":
                self._ui_gate(db, row["task_id"], except_key=key)
            changed = db.execute(
                "UPDATE operations SET state='submitting' WHERE operation_id=? AND state='prepared'", (key,)
            ).rowcount
            return bool(changed)

    def submitted(self, key: str, request_id: str, metadata: dict) -> OperationRecord:
        validate_payload(metadata)
        validate_payload(request_id)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (key,)).fetchone()
            if row is None or row["state"] not in {"submitting", "outcome_unknown", "submitted", "running"}:
                raise PipelineError("operation_state")
            if row["provider_request_id"] and row["provider_request_id"] != request_id:
                raise PipelineError("request_conflict", "Operation already references another provider request")
            db.execute(
                "UPDATE operations SET state='submitted',provider_request_id=?,result=? WHERE operation_id=?",
                (request_id, canonical_json(metadata), key),
            )
        return self.get(key)

    def record_interruption(self, key: str) -> None:
        """Audit a lost Activity acknowledgement without allowing late submission."""
        with self.transaction() as db:
            row = db.execute("SELECT state FROM operations WHERE operation_id=?", (key,)).fetchone()
            if row is None or row["state"] in {"succeeded", "failed"}:
                return
            if row["state"] == "prepared":
                # Serialized with begin_submit: no caller can submit this preparation
                # after we prove the effect boundary has not been crossed.
                db.execute(
                    """UPDATE operations SET state='failed',reserved_cost=0,reserved_gpu=0,result=?
                    WHERE operation_id=?""",
                    (canonical_json({"interrupted_before_submission": True}), key),
                )
                db.execute("DELETE FROM resources WHERE operation_id=?", (key,))
            else:
                db.execute("UPDATE operations SET state='outcome_unknown' WHERE operation_id=?", (key,))
                db.execute("UPDATE resources SET uncertain=1 WHERE operation_id=?", (key,))

    def uncertain(self, key: str) -> OperationRecord:
        with self.transaction() as db:
            db.execute(
                """UPDATE operations SET state='outcome_unknown'
                WHERE operation_id=? AND state NOT IN ('succeeded','failed')""",
                (key,),
            )
            db.execute("UPDATE resources SET uncertain=1 WHERE operation_id=?", (key,))
        return self.get(key)

    def finish(self, key: str, actual: Cost, result: dict, *, failed: bool = False) -> OperationRecord:
        validate_payload(result)
        actual_cost, actual_gpu = units(actual.cost_usd), units(actual.gpu_minutes)
        state = "failed" if failed else "succeeded"
        with self.transaction() as db:
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (key,)).fetchone()
            if row is None:
                raise PipelineError("unknown_operation")
            if row["state"] in {"succeeded", "failed"}:
                previous = json.loads(row["result"])
                previous.pop("budget_exceeded", None)
                incoming = dict(result)
                incoming.pop("budget_exceeded", None)
                if (row["actual_cost"], row["actual_gpu"], row["state"], canonical_json(previous)) != (
                    actual_cost,
                    actual_gpu,
                    state,
                    canonical_json(incoming),
                ):
                    raise PipelineError("settlement_conflict", "Operation already has a different final settlement")
                return self._record(row)
            result = dict(result)
            result["budget_exceeded"] = actual_cost > row["reserved_cost"] or actual_gpu > row["reserved_gpu"]
            db.execute(
                """UPDATE operations SET state=?,actual_cost=?,actual_gpu=?,
                reserved_cost=0,reserved_gpu=0,result=? WHERE operation_id=?""",
                (state, actual_cost, actual_gpu, canonical_json(result), key),
            )
            db.execute("DELETE FROM resources WHERE operation_id=?", (key,))
        return self.get(key)

    def list_operations(self, task_id: str) -> list[OperationRecord]:
        with self.transaction() as db:
            rows = db.execute(
                "SELECT * FROM operations WHERE task_id=? ORDER BY revision,step_id", (task_id,)
            ).fetchall()
        return [self._record(row) for row in rows]

    @staticmethod
    def _ui_gate(db, task_id: str, *, except_key: str | None = None) -> None:
        rows = db.execute("SELECT operation_id,state,result FROM operations WHERE task_id=?", (task_id,)).fetchall()
        for row in rows:
            if json.loads(row["result"]).get("usage_verdict") == "budget_exceeded":
                raise PipelineError("budget_exceeded", "Actual usage exceeded the approved UI budget")
        for row in rows:
            if row["operation_id"] != except_key and row["state"] in {
                "prepared",
                "submitting",
                "submitted",
                "running",
                "outcome_unknown",
            }:
                raise PipelineError("awaiting_reconciliation", "Resolve the current operation before new consumption")

    @staticmethod
    def _check_ui_binding(db, plan, step_id, revision, binding, request=None) -> None:
        from .registry import resolve_ui_step

        if db.execute("PRAGMA user_version").fetchone()[0] not in {2, 3}:
            raise PipelineError("migration_required", "Stop writers and migrate the UI ledger to v2")
        if binding is None or binding.step_id != step_id or binding.revision != revision or binding.outputs:
            raise PipelineError("invalid_step", "A frozen input binding without outputs is required")
        resolve_ui_step(plan, binding)
        old = db.execute(
            "SELECT input_hash FROM ui_step_bindings WHERE task_id=? AND step_id=? AND revision=?",
            (plan.task_id, step_id, revision),
        ).fetchone()
        if old and old["input_hash"] != binding.input_hash:
            raise PipelineError("step_conflict", "This step already has different frozen inputs")
        if not old:
            from ..schemas.ui import UICallLimits, UIResourceLimits

            limits = request.limits if request else UICallLimits()
            resources = request.resources if request else UIResourceLimits()
            maximum = {
                "ui.analyze": limits.vlm_calls_per_image,
                "ui.ocr": resources.ocr_max_tiles + limits.ocr_rereads_per_image,
                "ui.search": limits.search_attempts,
            }.get(binding.capability, 1)
            rows = db.execute("SELECT binding FROM ui_step_bindings WHERE task_id=?", (plan.task_id,)).fetchall()
            count = sum(json.loads(row["binding"])["capability"] == binding.capability for row in rows)
            if binding.capability == "ui.search":
                if step_id == "search":
                    if any(json.loads(row["binding"])["step_id"] == "search" for row in rows):
                        raise PipelineError("call_limit")
                    return
                family = (
                    "probe"
                    if step_id.startswith("search.probe.")
                    else "download"
                    if step_id.startswith("search.download.")
                    else "attempt"
                )
                maximum = {
                    "probe": 4,
                    "download": limits.queries * limits.downloads_per_query,
                    "attempt": limits.search_attempts,
                }[family]

                def same_family(row):
                    value = json.loads(row["binding"])
                    name = value["step_id"]
                    group = (
                        "probe"
                        if name.startswith("search.probe.")
                        else "download"
                        if name.startswith("search.download.")
                        else "attempt"
                    )
                    return value["capability"] == "ui.search" and group == family

                count = sum(same_family(row) for row in rows)
            if count >= maximum:
                raise PipelineError("call_limit", "The approved capability call limit is exhausted")

    def ui_binding(self, key: str):
        from ..schemas.ui import UIStepBinding

        with self.transaction() as db:
            row = db.execute(
                """SELECT b.binding,b.outputs FROM ui_step_bindings b JOIN operations o
                ON b.task_id=o.task_id AND b.step_id=o.step_id AND b.revision=o.revision
                WHERE o.operation_id=?""",
                (key,),
            ).fetchone()
        if row is None:
            raise PipelineError("unknown_step")
        return UIStepBinding.model_validate({**json.loads(row["binding"]), "outputs": json.loads(row["outputs"])})

    def finish_ui(self, key: str, actual: Cost | None, result: dict, *, failed: bool = False) -> OperationRecord:
        """Settle measured usage and its approval verdict in the same transaction."""
        if actual is None:
            return self.uncertain(key)
        actual = Cost.model_validate(actual.model_dump(mode="json"))
        validate_payload(result)
        actual_cost, actual_gpu = units(actual.cost_usd), units(actual.gpu_minutes)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (key,)).fetchone()
            if row is None:
                raise PipelineError("unknown_operation")
            task = db.execute("SELECT plan FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone()
            plan = PipelinePlan.model_validate_json(task["plan"])
            if plan.workflow_type != "ui_analysis":
                raise PipelineError("invalid_step", "UI settlement only applies to UI operations")
            if row["state"] in {"succeeded", "failed"}:
                previous = json.loads(row["result"])
                for name in ("usage_verdict", "budget_exceeded"):
                    previous.pop(name, None)
                incoming = {k: v for k, v in result.items() if k not in {"usage_verdict", "budget_exceeded"}}
                if (row["actual_cost"], row["actual_gpu"], previous) != (actual_cost, actual_gpu, incoming):
                    raise PipelineError("settlement_conflict")
                return self._record(row)
            spent = db.execute(
                """SELECT COALESCE(SUM(actual_cost),0) cost, COALESCE(SUM(actual_gpu),0) gpu
                FROM operations WHERE task_id=? AND operation_id<>?""",
                (plan.task_id, key),
            ).fetchone()
            budget = plan.envelope.budget
            exceeded = (
                actual_cost > units(budget.max_iteration_cost_usd, limit=True)
                or actual_gpu > units(budget.max_iteration_gpu_minutes, limit=True)
                or spent["cost"] + actual_cost > units(budget.max_total_cost_usd, limit=True)
                or spent["gpu"] + actual_gpu > units(budget.max_total_gpu_minutes, limit=True)
            )
            adjusted = actual_cost > row["reserved_cost"] or actual_gpu > row["reserved_gpu"]
            verdict = "budget_exceeded" if exceeded else "reservation_adjusted" if adjusted else "within_budget"
            payload = {**result, "usage_verdict": verdict, "budget_exceeded": adjusted}
            validate_payload(payload)
            from ..schemas.pipeline import ArtifactRef

            outputs = [ArtifactRef.model_validate(ref) for ref in result.get("artifacts", [])]
            if any(ref.task_id != plan.task_id or ref.operation_id != key for ref in outputs):
                raise PipelineError("artifact_scope")
            db.execute(
                """UPDATE operations SET state=?,actual_cost=?,actual_gpu=?,
                reserved_cost=0,reserved_gpu=0,result=? WHERE operation_id=?""",
                (
                    "failed" if failed or exceeded else "succeeded",
                    actual_cost,
                    actual_gpu,
                    canonical_json(payload),
                    key,
                ),
            )
            db.execute(
                "UPDATE ui_step_bindings SET outputs=? WHERE task_id=? AND step_id=? AND revision=?",
                (
                    canonical_json([ref.model_dump(mode="json") for ref in outputs]),
                    row["task_id"],
                    row["step_id"],
                    row["revision"],
                ),
            )
            if db.execute("PRAGMA user_version").fetchone()[0] == 3:
                quota_units = result.get("quota_units")
                if type(quota_units) is int and quota_units >= 0:
                    db.execute(
                        "UPDATE quota_reservations SET units=?,state='settled' WHERE operation_id=?", (quota_units, key)
                    )
                db.execute("UPDATE quota_probes SET state='settled' WHERE operation_id=?", (key,))
            db.execute("DELETE FROM resources WHERE operation_id=?", (key,))
            return self._record(db.execute("SELECT * FROM operations WHERE operation_id=?", (key,)).fetchone())

    def usage(self, task_id: str) -> dict[str, Cost]:
        buckets = {"actual": [0, 0], "reserved": [0, 0], "unsettled": [0, 0]}
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM operations WHERE task_id=?", (task_id,)).fetchall()
        for row in rows:
            buckets["actual"][0] += row["actual_cost"]
            buckets["actual"][1] += row["actual_gpu"]
            kind = "unsettled" if row["state"] == "outcome_unknown" else "reserved"
            buckets[kind][0] += row["reserved_cost"]
            buckets[kind][1] += row["reserved_gpu"]
        return {
            kind: Cost(cost_usd=values[0] / SCALE, gpu_minutes=values[1] / SCALE) for kind, values in buckets.items()
        }

    @staticmethod
    def _record(row: sqlite3.Row) -> OperationRecord:
        return OperationRecord(
            operation_id=row["operation_id"],
            task_id=row["task_id"],
            step_id=row["step_id"],
            revision=row["revision"],
            input_hash=row["input_hash"],
            state=row["state"],
            provider_request_id=row["provider_request_id"],
            reserved=Cost(cost_usd=row["reserved_cost"] / SCALE, gpu_minutes=row["reserved_gpu"] / SCALE),
            actual=Cost(cost_usd=row["actual_cost"] / SCALE, gpu_minutes=row["actual_gpu"] / SCALE),
            result=json.loads(row["result"]),
        )
