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
            if version not in {0, 1, 2, 3, 4, 5}:
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
                    if initialize_ui in {3, 4, 5}:
                        from .migrations import _upgrade_v3

                        _upgrade_v3(db)
                    if initialize_ui in {4, 5}:
                        from .migrations import _upgrade_v4

                        _upgrade_v4(db)
                    if initialize_ui == 5:
                        from .migrations import _upgrade_v5

                        _upgrade_v5(db)

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
            if plan.workflow_type == "ui_analysis" and db.execute("PRAGMA user_version").fetchone()[0] >= 5:
                db.execute(
                    """INSERT OR IGNORE INTO ui_budget_groups
                    (root_task_id,budget) VALUES(?,?)""",
                    (plan.task_id, canonical_json(plan.envelope.budget)),
                )

    def register_child(
        self,
        plan: PipelinePlan,
        *,
        parent_task_id: str,
        root_task_id: str,
        purpose: str,
        source_ids: list[str],
        request_ref,
        selection_ref,
        selection_revision: int,
        source_chain: list[str] | None = None,
        edit_chain: list[str] | None = None,
    ) -> None:
        """Atomically persist a child task and its immutable budget binding."""
        from .registry import validate_registration

        validate_registration(plan)
        if plan.workflow_type not in {"ui_segmentation", "ui_inpaint"}:
            raise PipelineError("invalid_child")
        with self.transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] < 5:
                raise PipelineError("migration_required", "The UI ledger must be migrated to v5")
            parent = db.execute("SELECT plan FROM tasks WHERE task_id=?", (parent_task_id,)).fetchone()
            group = db.execute(
                "SELECT budget FROM ui_budget_groups WHERE root_task_id=?", (root_task_id,)
            ).fetchone()
            if parent is None or group is None:
                raise PipelineError("unknown_parent", "Parent task or root budget group is unknown")
            if self._root_task_id(db, parent_task_id) != root_task_id or parent_task_id == plan.task_id:
                raise PipelineError("child_cycle", "Child parent does not belong to the same root")
            old = db.execute("SELECT fingerprint FROM tasks WHERE task_id=?", (plan.task_id,)).fetchone()
            if old and old["fingerprint"] != plan.fingerprint:
                raise PipelineError("task_conflict", "Task ID already belongs to a different immutable plan")
            db.execute(
                "INSERT OR IGNORE INTO tasks(task_id,fingerprint,plan) VALUES(?,?,?)",
                (plan.task_id, plan.fingerprint, canonical_json(plan)),
            )
            old_binding = db.execute(
                "SELECT fingerprint FROM ui_child_bindings WHERE task_id=?", (plan.task_id,)
            ).fetchone()
            if old_binding:
                if old_binding["fingerprint"] != plan.fingerprint:
                    raise PipelineError("task_conflict")
                return
            db.execute(
                """INSERT INTO ui_child_bindings
                (task_id,parent_task_id,root_task_id,purpose,source_ids,request_ref,
                 selection_ref,selection_revision,selection_hash,budget,status,active,
                 fingerprint,source_chain,edit_chain)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    plan.task_id,
                    parent_task_id,
                    root_task_id,
                    purpose,
                    canonical_json(source_ids),
                    canonical_json(request_ref.model_dump(mode="json")),
                    canonical_json(selection_ref.model_dump(mode="json")),
                    selection_revision,
                    selection_ref.sha256,
                    canonical_json(plan.envelope.budget),
                    "pending",
                    0,
                    plan.fingerprint,
                    canonical_json(source_chain or source_ids),
                    canonical_json(edit_chain or []),
                ),
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
            if approved:
                plan_row = db.execute("SELECT plan FROM tasks WHERE task_id=?", (request.task_id,)).fetchone()
                plan = PipelinePlan.model_validate_json(plan_row["plan"])
                if self._is_child_workflow(plan.workflow_type):
                    self._activate_child_approval(db, request.task_id)
            db.execute("UPDATE approvals SET consumed=1 WHERE request_id=?", (request.request_id,))
            db.execute("UPDATE tasks SET approved=? WHERE task_id=?", (int(approved), request.task_id))
        return approved

    def _activate_child_approval(self, db: sqlite3.Connection, task_id: str) -> None:
        """Select one child version while holding the ledger write lock."""
        current = self._child_binding(db, task_id)
        if current["status"] in {"selection_superseded", "completed"}:
            raise PipelineError("selection_superseded", "This child selection is no longer current")
        root = current["root_task_id"]
        group = db.execute(
            "SELECT submission_gate FROM ui_budget_groups WHERE root_task_id=?", (root,)
        ).fetchone()
        root_task = db.execute("SELECT cancelled FROM tasks WHERE task_id=?", (root,)).fetchone()
        if not group or group["submission_gate"] != "open" or (root_task and root_task["cancelled"]):
            raise PipelineError("cancelled", "The root pipeline is cancelling")
        pending = db.execute(
            """SELECT task_id FROM ui_child_bindings
            WHERE root_task_id=? AND task_id<>?
              AND status IN ('pending','active')""",
            (root, task_id),
        ).fetchall()
        current_sources = set(json.loads(current["source_chain"]))
        replaceable = []
        for row in pending:
            older = self._child_binding(db, row["task_id"])
            older_sources = set(json.loads(older["source_chain"]))
            if not current_sources.intersection(older_sources):
                continue
            same_selection = (
                older_sources == current_sources
                and older["selection_revision"] == current["selection_revision"]
                and older["selection_hash"] == current["selection_hash"]
            )
            # Segmentation and inpaint may both be approved for one frozen
            # selection.  A changed selection invalidates pending children
            # across purposes on the same source/edit chain.
            if same_selection and older["purpose"] != current["purpose"]:
                continue
            operations = db.execute(
                """SELECT operation_id,state FROM operations WHERE task_id=?""",
                (row["task_id"],),
            ).fetchall()
            if any(item["state"] in {"submitting", "submitted", "running", "outcome_unknown"} for item in operations):
                raise PipelineError("awaiting_reconciliation", "An earlier child is still in flight")
            # A terminal child is historical evidence.  A newer selection may
            # coexist with it; it must not rewrite that history.
            if any(item["state"] in {"succeeded", "failed"} for item in operations):
                db.execute(
                    "UPDATE ui_child_bindings SET status='completed',active=0 WHERE task_id=?",
                    (row["task_id"],),
                )
                continue
            replaceable.append(row)
        # Prepared work has not crossed the provider boundary. Release its
        # reservation in this same transaction, retaining an auditable failed row.
        for row in replaceable:
            operations = db.execute("SELECT state FROM operations WHERE task_id=?", (row["task_id"],)).fetchall()
            if any(item["state"] in {"succeeded", "failed"} for item in operations):
                continue
            prepared = db.execute(
                """SELECT operation_id FROM operations WHERE task_id=? AND state='prepared'""",
                (row["task_id"],),
            ).fetchall()
            for operation in prepared:
                db.execute(
                    """UPDATE operations SET state='failed',reserved_cost=0,reserved_gpu=0,
                    result=? WHERE operation_id=?""",
                    (canonical_json({"selection_superseded": True}), operation["operation_id"]),
                )
                db.execute("DELETE FROM resources WHERE operation_id=?", (operation["operation_id"],))
                self._sync_charge(db, operation["operation_id"], "failed")
            db.execute(
                "UPDATE ui_child_bindings SET status='selection_superseded',active=0 WHERE task_id=?",
                (row["task_id"],),
            )
            db.execute("UPDATE tasks SET approved=0 WHERE task_id=?", (row["task_id"],))
        db.execute(
            "UPDATE ui_child_bindings SET status='active',active=1 WHERE task_id=?", (task_id,)
        )

    def request_cancel(self, task_id: str) -> None:
        with self.transaction() as db:
            root = self._root_task_id(db, task_id)
            db.execute("UPDATE tasks SET cancelled=1 WHERE task_id=?", (root,))
            if db.execute("PRAGMA user_version").fetchone()[0] >= 5:
                db.execute(
                    "UPDATE tasks SET cancelled=1 WHERE task_id IN "
                    "(SELECT task_id FROM ui_child_bindings WHERE root_task_id=?)",
                    (root,),
                )
                db.execute(
                    "UPDATE ui_budget_groups SET submission_gate='closed',stop_reason='cancelled' "
                    "WHERE root_task_id=?",
                    (root,),
                )

    @staticmethod
    def _root_task_id(db: sqlite3.Connection, task_id: str) -> str:
        if db.execute("PRAGMA user_version").fetchone()[0] < 5:
            return task_id
        row = db.execute("SELECT root_task_id FROM ui_child_bindings WHERE task_id=?", (task_id,)).fetchone()
        return row["root_task_id"] if row else task_id

    @staticmethod
    def _is_child_workflow(workflow_type: str) -> bool:
        return workflow_type in {"ui_segmentation", "ui_inpaint"}

    @staticmethod
    def _child_binding(db: sqlite3.Connection, task_id: str):
        row = db.execute("SELECT * FROM ui_child_bindings WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise PipelineError("unknown_child", "Unknown UI child binding")
        return row

    @staticmethod
    def _child_step_check(db, plan, step_id, revision, binding) -> None:
        from .registry import resolve_ui_step

        if binding is None or binding.task_id != plan.task_id or binding.revision != revision or binding.outputs:
            raise PipelineError("invalid_step", "A frozen child input binding without outputs is required")
        expected = "ui.segment" if plan.workflow_type == "ui_segmentation" else "ui.inpaint"
        if binding.capability != expected:
            raise PipelineError("prohibited_capability")
        child = Ledger._child_binding(db, plan.task_id)
        frozen_selection = json.loads(child["selection_ref"])
        if (
            binding.selection_ref is None
            or binding.selection_ref.sha256 != frozen_selection["sha256"]
            or binding.selection_revision != child["selection_revision"]
            or binding.selection_hash != frozen_selection["sha256"]
        ):
            raise PipelineError("selection_conflict", "Child step selection does not match its frozen child")
        frozen_inputs = [ref.model_dump(mode="json") for ref in plan.inputs]
        bound_inputs = [ref.model_dump(mode="json") for ref in binding.inputs]
        if bound_inputs != frozen_inputs:
            raise PipelineError("step_conflict", "Child step inputs differ from the approved child plan")
        resolve_ui_step(plan, binding)
        old = db.execute(
            "SELECT input_hash FROM ui_step_bindings WHERE task_id=? AND step_id=? AND revision=?",
            (plan.task_id, step_id, revision),
        ).fetchone()
        if old and old["input_hash"] != binding.input_hash:
            raise PipelineError("step_conflict", "This child step already has different frozen inputs")

    @staticmethod
    def _child_revision_context(db, task_id: str):
        row = db.execute(
            """SELECT root_task_id,purpose,source_chain,edit_chain FROM ui_child_bindings
            WHERE task_id=?""",
            (task_id,),
        ).fetchone()
        if row is None:
            raise PipelineError("unknown_child")
        return row

    @staticmethod
    def _sync_charge(db, operation_id_value: str, state: str) -> None:
        if db.execute("PRAGMA user_version").fetchone()[0] >= 5:
            db.execute(
                "UPDATE ui_operation_charges SET charge_state=? WHERE operation_id=?",
                (state, operation_id_value),
            )
            row = db.execute(
                "SELECT root_task_id FROM ui_operation_charges WHERE operation_id=?", (operation_id_value,)
            ).fetchone()
            if row:
                Ledger._refresh_revision_counts(db, row["root_task_id"])

    @staticmethod
    def _charged_revisions(db, root_task_id):
        # Only proof of no provider submission releases a reserved revision.
        # Failed, submitted and unknown calls remain charged across new child IDs.
        return db.execute(
            """SELECT c.* FROM ui_operation_charges c JOIN operations o USING(operation_id)
            WHERE c.root_task_id=? AND NOT (o.state='failed' AND
              (COALESCE(json_extract(o.result,'$.selection_superseded'),0)=1 OR
               COALESCE(json_extract(o.result,'$.interrupted_before_submission'),0)=1))""",
            (root_task_id,),
        ).fetchall()

    @staticmethod
    def _refresh_revision_counts(db, root_task_id):
        charges = Ledger._charged_revisions(db, root_task_id)
        used_by_source = {}
        for charge in charges:
            for source in json.loads(charge["source_ids"]):
                used_by_source[source] = used_by_source.get(source, 0) + charge["revision_units"]
        db.execute(
            "UPDATE ui_budget_groups SET revision_count=?,source_limits=? WHERE root_task_id=?",
            (sum(row["revision_units"] for row in charges), canonical_json(used_by_source), root_task_id),
        )

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
        child = self._is_child_workflow(plan.workflow_type)
        with self.transaction() as db:
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (plan.task_id,)).fetchone()
            if not task or task["fingerprint"] != plan.fingerprint:
                raise PipelineError("approval_required", "A consumed exact approval is required")
            if child:
                binding = self._child_binding(db, plan.task_id)
                if binding["status"] == "selection_superseded":
                    raise PipelineError("selection_superseded", "A newer selection replaced this child")
            if not task["approved"]:
                raise PipelineError("approval_required", "A consumed exact approval is required")
            if task["cancelled"]:
                raise PipelineError("cancelled", "The pipeline is cancelling")
            if plan.workflow_type == "ui_analysis":
                self._check_ui_binding(db, plan, step_id, revision, ui_binding, ui_request)
            elif child:
                self._child_step_check(db, plan, step_id, revision, ui_binding)
            old = db.execute("SELECT * FROM operations WHERE operation_id=?", (key,)).fetchone()
            if old:
                return self._record(old)
            if child:
                binding = self._child_binding(db, plan.task_id)
                if binding["status"] != "active" or not binding["active"]:
                    if binding["status"] == "selection_superseded":
                        raise PipelineError("selection_superseded", "A newer selection replaced this child")
                    raise PipelineError("awaiting_approval", "The exact child selection is not active")
            if plan.workflow_type == "ui_analysis" or child:
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
            root_task_id = self._root_task_id(db, plan.task_id) if child else plan.task_id
            grouped_root = False
            if db.execute("PRAGMA user_version").fetchone()[0] >= 5:
                grouped_root = db.execute(
                    "SELECT 1 FROM ui_budget_groups WHERE root_task_id=?", (plan.task_id,)
                ).fetchone() is not None
            aggregate_children = child or grouped_root
            if aggregate_children:
                spent = db.execute(
                    """SELECT COALESCE(SUM(o.reserved_cost+o.actual_cost),0) AS cost,
                    COALESCE(SUM(o.reserved_gpu+o.actual_gpu),0) AS gpu FROM operations o
                    WHERE o.task_id=? OR o.task_id IN
                        (SELECT task_id FROM ui_child_bindings WHERE root_task_id=?)""",
                    (root_task_id, root_task_id),
                ).fetchone()
            else:
                spent = db.execute(
                    """SELECT COALESCE(SUM(reserved_cost+actual_cost),0) AS cost,
                    COALESCE(SUM(reserved_gpu+actual_gpu),0) AS gpu FROM operations WHERE task_id=?""",
                    (root_task_id,),
                ).fetchone()
            root_plan_row = db.execute("SELECT plan FROM tasks WHERE task_id=?", (root_task_id,)).fetchone()
            root_budget = budget
            if root_plan_row is not None:
                root_budget = PipelinePlan.model_validate_json(root_plan_row["plan"]).envelope.budget
            if (
                spent["cost"] + reserve_cost > units(root_budget.max_total_cost_usd, limit=True)
                or spent["gpu"] + reserve_gpu > units(root_budget.max_total_gpu_minutes, limit=True)
            ):
                raise PipelineError(
                    "budget_insufficient" if child else "total_budget",
                    "Total budget, including uncertain usage, would be exceeded",
                )
            if child:
                child_spent = db.execute(
                    """SELECT COALESCE(SUM(reserved_cost+actual_cost),0) AS cost,
                    COALESCE(SUM(reserved_gpu+actual_gpu),0) AS gpu FROM operations WHERE task_id=?""",
                    (plan.task_id,),
                ).fetchone()
                if child_spent["cost"] + reserve_cost > units(budget.max_total_cost_usd, limit=True) or child_spent[
                    "gpu"
                ] + reserve_gpu > units(budget.max_total_gpu_minutes, limit=True):
                    raise PipelineError("budget_insufficient", "Child budget would be exceeded")
                context = self._child_revision_context(db, plan.task_id)
                charges = self._charged_revisions(db, root_task_id)
                source_set = set(json.loads(context["source_chain"]))
                capability = "ui.segment" if plan.workflow_type == "ui_segmentation" else "ui.inpaint"
                prior = any(
                    row["capability"] == capability
                    and source_set.intersection(json.loads(row["source_chain"]))
                    for row in charges
                )
                revision_units = int(revision > 0 or prior)
                used = sum(row["revision_units"] for row in charges)
                if used + revision_units > root_budget.max_revisions:
                    raise PipelineError("revision_limit", "The root generation revision budget is exhausted")
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
            if child:
                context = self._child_revision_context(db, plan.task_id)
                db.execute(
                    """INSERT INTO ui_operation_charges
                    (operation_id,root_task_id,task_id,capability,source_ids,source_chain,edit_chain,
                     revision,revision_units,charge_state)
                    SELECT ?,root_task_id,task_id,?,source_ids,source_chain,edit_chain,?,?,'reserved'
                    FROM ui_child_bindings WHERE task_id=?""",
                    (
                        key,
                        "ui.segment" if plan.workflow_type == "ui_segmentation" else "ui.inpaint",
                        revision,
                        revision_units,
                        plan.task_id,
                    ),
                )
                self._refresh_revision_counts(db, root_task_id)
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
            workflow_type = json.loads(plan["plan"])["workflow_type"]
            if workflow_type == "ui_analysis" or self._is_child_workflow(workflow_type):
                self._ui_gate(db, row["task_id"], except_key=key)
            if self._is_child_workflow(workflow_type):
                binding = self._child_binding(db, row["task_id"])
                if binding["status"] != "active" or not binding["active"]:
                    raise PipelineError("selection_superseded", "This child selection is no longer active")
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
            self._sync_charge(db, key, "submitted")
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
                self._sync_charge(db, key, "failed")
            else:
                db.execute("UPDATE operations SET state='outcome_unknown' WHERE operation_id=?", (key,))
                db.execute("UPDATE resources SET uncertain=1 WHERE operation_id=?", (key,))
                self._sync_charge(db, key, "outcome_unknown")

    def uncertain(self, key: str) -> OperationRecord:
        with self.transaction() as db:
            db.execute(
                """UPDATE operations SET state='outcome_unknown'
                WHERE operation_id=? AND state NOT IN ('succeeded','failed')""",
                (key,),
            )
            db.execute("UPDATE resources SET uncertain=1 WHERE operation_id=?", (key,))
            self._sync_charge(db, key, "outcome_unknown")
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
                if row["state"] == "failed" and previous.get("usage_verdict") == "budget_exceeded":
                    state = "failed"
                for derived in ("budget_exceeded", "usage_verdict"):
                    previous.pop(derived, None)
                incoming = dict(result)
                for derived in ("budget_exceeded", "usage_verdict"):
                    incoming.pop(derived, None)
                if (row["actual_cost"], row["actual_gpu"], row["state"], canonical_json(previous)) != (
                    actual_cost,
                    actual_gpu,
                    state,
                    canonical_json(incoming),
                ):
                    raise PipelineError("settlement_conflict", "Operation already has a different final settlement")
                return self._record(row)
            plan_row = db.execute("SELECT plan FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone()
            child = plan_row is not None and self._is_child_workflow(
                json.loads(plan_row["plan"])["workflow_type"]
            )
            if child:
                root = self._root_task_id(db, row["task_id"])
                root_plan = PipelinePlan.model_validate_json(
                    db.execute("SELECT plan FROM tasks WHERE task_id=?", (root,)).fetchone()["plan"]
                )
                child_plan = PipelinePlan.model_validate_json(plan_row["plan"])
                previous = db.execute(
                    """SELECT COALESCE(SUM(o.actual_cost),0) cost,COALESCE(SUM(o.actual_gpu),0) gpu
                    FROM operations o WHERE (o.task_id=? OR o.task_id IN
                        (SELECT task_id FROM ui_child_bindings WHERE root_task_id=?))
                      AND o.operation_id<>?""",
                    (root, root, key),
                ).fetchone()
                if (
                    actual_cost > units(root_plan.envelope.budget.max_iteration_cost_usd, limit=True)
                    or actual_gpu > units(root_plan.envelope.budget.max_iteration_gpu_minutes, limit=True)
                    or actual_cost > units(child_plan.envelope.budget.max_iteration_cost_usd, limit=True)
                    or actual_gpu > units(child_plan.envelope.budget.max_iteration_gpu_minutes, limit=True)
                    or previous["cost"] + actual_cost > units(root_plan.envelope.budget.max_total_cost_usd, limit=True)
                    or previous["gpu"] + actual_gpu > units(root_plan.envelope.budget.max_total_gpu_minutes, limit=True)
                ):
                    failed = True
                    result = {**result, "usage_verdict": "budget_exceeded"}
                    state = "failed"
                child_previous = db.execute(
                    """SELECT COALESCE(SUM(actual_cost),0) cost,COALESCE(SUM(actual_gpu),0) gpu
                    FROM operations WHERE task_id=? AND operation_id<>?""",
                    (row["task_id"], key),
                ).fetchone()
                if (
                    child_previous["cost"] + actual_cost
                    > units(child_plan.envelope.budget.max_total_cost_usd, limit=True)
                    or child_previous["gpu"] + actual_gpu
                    > units(child_plan.envelope.budget.max_total_gpu_minutes, limit=True)
                ):
                    failed = True
                    result = {**result, "usage_verdict": "budget_exceeded"}
                    state = "failed"
                if result.get("usage_verdict") == "budget_exceeded":
                    db.execute(
                        "UPDATE ui_budget_groups SET submission_gate='closed',stop_reason='budget_exceeded' "
                        "WHERE root_task_id=?",
                        (root,),
                    )
                else:
                    adjusted = actual_cost > row["reserved_cost"] or actual_gpu > row["reserved_gpu"]
                    result = {**result, "usage_verdict": "reservation_adjusted" if adjusted else "within_budget"}
            result = dict(result)
            result["budget_exceeded"] = actual_cost > row["reserved_cost"] or actual_gpu > row["reserved_gpu"]
            db.execute(
                """UPDATE operations SET state=?,actual_cost=?,actual_gpu=?,
                reserved_cost=0,reserved_gpu=0,result=? WHERE operation_id=?""",
                (state, actual_cost, actual_gpu, canonical_json(result), key),
            )
            db.execute("DELETE FROM resources WHERE operation_id=?", (key,))
            self._sync_charge(db, key, state)
        return self.get(key)

    def list_operations(self, task_id: str) -> list[OperationRecord]:
        with self.transaction() as db:
            rows = db.execute(
                "SELECT * FROM operations WHERE task_id=? ORDER BY revision,step_id", (task_id,)
            ).fetchall()
        return [self._record(row) for row in rows]

    @staticmethod
    def _ui_gate(db, task_id: str, *, except_key: str | None = None) -> None:
        root = Ledger._root_task_id(db, task_id)
        if db.execute("PRAGMA user_version").fetchone()[0] >= 5:
            gate = db.execute(
                "SELECT submission_gate,stop_reason FROM ui_budget_groups WHERE root_task_id=?", (root,)
            ).fetchone()
            if gate and gate["submission_gate"] != "open":
                reason = gate["stop_reason"] or "The root pipeline is closed"
                raise PipelineError("budget_exceeded" if reason == "budget_exceeded" else "cancelled", reason)
        if db.execute("PRAGMA user_version").fetchone()[0] >= 5:
            rows = db.execute(
                """SELECT o.operation_id,o.state,o.result FROM operations o
                WHERE o.task_id=? OR o.task_id IN
                    (SELECT task_id FROM ui_child_bindings WHERE root_task_id=?)""",
                (root, root),
            ).fetchall()
        else:
            rows = db.execute("SELECT operation_id,state,result FROM operations WHERE task_id=?", (root,)).fetchall()
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

        if db.execute("PRAGMA user_version").fetchone()[0] not in {2, 3, 4, 5}:
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
            child = self._is_child_workflow(plan.workflow_type)
            if plan.workflow_type != "ui_analysis" and not child:
                raise PipelineError("invalid_step", "UI settlement only applies to UI operations")
            if row["state"] in {"succeeded", "failed"}:
                previous = json.loads(row["result"])
                for name in ("usage_verdict", "budget_exceeded"):
                    previous.pop(name, None)
                incoming = {k: v for k, v in result.items() if k not in {"usage_verdict", "budget_exceeded"}}
                if (row["actual_cost"], row["actual_gpu"], previous) != (actual_cost, actual_gpu, incoming):
                    raise PipelineError("settlement_conflict")
                return self._record(row)
            root_task_id = self._root_task_id(db, plan.task_id) if child else plan.task_id
            grouped_root = child
            if db.execute("PRAGMA user_version").fetchone()[0] >= 5:
                grouped_root = (
                    db.execute(
                        "SELECT 1 FROM ui_budget_groups WHERE root_task_id=?", (root_task_id,)
                    ).fetchone()
                    is not None
                )
            if grouped_root:
                spent = db.execute(
                    """SELECT COALESCE(SUM(o.actual_cost),0) cost, COALESCE(SUM(o.actual_gpu),0) gpu
                    FROM operations o WHERE (o.task_id=? OR o.task_id IN
                        (SELECT task_id FROM ui_child_bindings WHERE root_task_id=?))
                      AND o.operation_id<>?""",
                    (root_task_id, root_task_id, key),
                ).fetchone()
            else:
                spent = db.execute(
                    """SELECT COALESCE(SUM(actual_cost),0) cost, COALESCE(SUM(actual_gpu),0) gpu
                    FROM operations WHERE task_id=? AND operation_id<>?""",
                    (plan.task_id, key),
                ).fetchone()
            budget = plan.envelope.budget
            root_budget = budget
            if child:
                root_plan_row = db.execute(
                    "SELECT plan FROM tasks WHERE task_id=?", (root_task_id,)
                ).fetchone()
                if root_plan_row is None:
                    raise PipelineError("unknown_parent")
                root_budget = PipelinePlan.model_validate_json(root_plan_row["plan"]).envelope.budget
                child_spent = db.execute(
                    """SELECT COALESCE(SUM(actual_cost),0) cost, COALESCE(SUM(actual_gpu),0) gpu
                    FROM operations WHERE task_id=? AND operation_id<>?""",
                    (plan.task_id, key),
                ).fetchone()
            else:
                child_spent = spent
            exceeded = (
                actual_cost > units(budget.max_iteration_cost_usd, limit=True)
                or actual_gpu > units(budget.max_iteration_gpu_minutes, limit=True)
                or child_spent["cost"] + actual_cost > units(budget.max_total_cost_usd, limit=True)
                or child_spent["gpu"] + actual_gpu > units(budget.max_total_gpu_minutes, limit=True)
                or spent["cost"] + actual_cost > units(root_budget.max_total_cost_usd, limit=True)
                or spent["gpu"] + actual_gpu > units(root_budget.max_total_gpu_minutes, limit=True)
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
            if exceeded and grouped_root:
                db.execute(
                    "UPDATE ui_budget_groups SET submission_gate='closed',stop_reason='budget_exceeded' "
                    "WHERE root_task_id=?",
                    (root_task_id,),
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
            if db.execute("PRAGMA user_version").fetchone()[0] in {3, 4, 5}:
                quota_units = result.get("quota_units")
                if type(quota_units) is int and quota_units >= 0:
                    db.execute(
                        "UPDATE quota_reservations SET units=?,state='settled' WHERE operation_id=?", (quota_units, key)
                    )
                db.execute("UPDATE quota_probes SET state='settled' WHERE operation_id=?", (key,))
            if child:
                terminal_state = "failed" if failed or exceeded else "succeeded"
                self._sync_charge(db, key, terminal_state)
                db.execute(
                    "UPDATE ui_child_bindings SET status='completed',active=0 WHERE task_id=?",
                    (plan.task_id,),
                )
            db.execute("DELETE FROM resources WHERE operation_id=?", (key,))
            return self._record(db.execute("SELECT * FROM operations WHERE operation_id=?", (key,)).fetchone())

    def usage(self, task_id: str, *, include_children: bool = False) -> dict[str, Cost]:
        buckets = {"actual": [0, 0], "reserved": [0, 0], "unsettled": [0, 0]}
        with self.transaction() as db:
            if include_children and db.execute("PRAGMA user_version").fetchone()[0] >= 5:
                root = self._root_task_id(db, task_id)
                rows = db.execute(
                    """SELECT * FROM operations WHERE task_id=? OR task_id IN
                    (SELECT task_id FROM ui_child_bindings WHERE root_task_id=?)""",
                    (root, root),
                ).fetchall()
            else:
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
