"""Explicit cloud image retry checks under the existing v5 ledger transaction.

Budget grants live in immutable child plans and consumed approval receipts, not
in rewritten roots, replacement ledgers or cleared unknown operations.
"""
import json
from decimal import Decimal

from ..schemas.pipeline import PipelinePlan
from ..schemas.ui_cloud import CloudImageRetry
from .errors import PipelineError


def retry_binding(plan):
    value = plan.parameters.get("cloud_retry")
    if value is None:
        return None
    if plan.workflow_type != "ui_cloud_inpaint":
        raise PipelineError("invalid_cloud_retry")
    return CloudImageRetry.model_validate(value)


def root_budget(db, root_id):
    row = db.execute("SELECT plan FROM tasks WHERE task_id=?", (root_id,)).fetchone()
    if row is None:
        raise PipelineError("operation_scope")
    root = PipelinePlan.model_validate_json(row["plan"])
    return root.envelope.budget


def retry_plans(db, root_id):
    rows = db.execute("""SELECT t.plan FROM tasks t JOIN ui_child_bindings b USING(task_id)
        WHERE b.root_task_id=? AND b.purpose='cloud_inpaint'""", (root_id,)).fetchall()
    return [p for row in rows if (p := PipelinePlan.model_validate_json(row["plan"])).parameters.get("cloud_retry")]


def grant_consumed(db, task_id):
    return db.execute("""SELECT 1 FROM approvals WHERE task_id=? AND consumed=1
        AND json_extract(payload,'$.decision')='approve'""", (task_id,)).fetchone() is not None


def effective_budget(db, root_id):
    budget = root_budget(db, root_id)
    missing = False
    for index, plan in enumerate(retry_chain(db, root_id)):
        if not grant_consumed(db, plan.task_id):
            missing = True
            continue
        if missing:
            raise PipelineError("cloud_retry_conflict", "Retry grants must be consumed in chain order")
        binding = retry_binding(plan)
        validate_grant(binding, budget, plan.envelope.budget.max_iteration_cost_usd, followup=index == 1)
        budget = binding.budget_after
    return budget


def retry_chain(db, root_id, extra=None):
    """At most two linked retries; never select by creation time or allow forks."""
    plans = {p.task_id: p for p in retry_plans(db, root_id)}
    if extra is not None:
        plans[extra.task_id] = extra
    if not plans:
        return []
    if len(plans) > 2:
        raise PipelineError("cloud_retry_conflict", "At most two explicit retries are supported")
    first = [p for p in plans.values() if retry_binding(p).base_task_id not in plans]
    if len(first) != 1:
        raise PipelineError("cloud_retry_conflict")
    chain = first
    rest = [p for p in plans.values() if p.task_id != first[0].task_id]
    if rest:
        if retry_binding(rest[0]).base_task_id != first[0].task_id:
            raise PipelineError("cloud_retry_conflict")
        chain += rest
    return chain


def retry_history(db, plan):
    chain = retry_chain(db, plan.parameters["root_task_id"], plan)
    index = next(i for i, p in enumerate(chain) if p.task_id == plan.task_id)
    return [retry_binding(p) for p in chain[:index + 1]]


def validate_grant(binding, before, amount, *, followup=False):
    changes = {
        "max_total_cost_usd": float(Decimal(str(before.max_total_cost_usd)) + Decimal(str(amount))),
        "max_revisions": before.max_revisions + 1,
    }
    if followup:
        changes["max_iteration_cost_usd"] = amount
    expected = before.model_copy(update=changes)
    if binding.budget_before != before or binding.budget_after != expected:
        raise PipelineError("budget_scope", "Retry must explicitly grant exactly one extra image attempt")


def validate_retry(db, plan, *, require_consumed=False):
    """Only named finished image unknowns in this bounded chain may coexist."""
    binding = retry_binding(plan)
    if binding is None:
        return None
    root_id = plan.parameters["root_task_id"]
    history = retry_history(db, plan)
    previous_budget = root_budget(db, root_id)
    for index, item in enumerate(history):
        old = db.execute("SELECT * FROM operations WHERE operation_id=?", (item.base_operation_id,)).fetchone()
        base_row = db.execute("""SELECT t.plan,b.root_task_id,b.selection_revision FROM tasks t
            JOIN ui_child_bindings b USING(task_id) WHERE t.task_id=?""", (item.base_task_id,)).fetchone()
        if old is None or base_row is None or base_row["root_task_id"] != root_id:
            raise PipelineError("operation_scope")
        base = PipelinePlan.model_validate_json(base_row["plan"])
        if (base.workflow_type != "ui_cloud_inpaint" or bool(retry_binding(base)) != bool(index)
                or old["task_id"] != base.task_id or old["state"] != "outcome_unknown"
                or old["reserved_gpu"] or old["actual_gpu"]
                or base_row["selection_revision"] != plan.parameters["selection_revision"]):
            raise PipelineError("cloud_retry_not_available")
        result = json.loads(old["result"])
        trace = result.get("submission_trace", {})
        if (trace.get("stage") != "cloud_inpaint" or not trace.get("finished_at")
                or result.get("receipt") or result.get("artifacts") or trace.get("output_ref")):
            raise PipelineError("cloud_retry_not_available", "Collect existing results before another call")
        if db.execute("SELECT 1 FROM resources WHERE operation_id=?", (old["operation_id"],)).fetchone():
            raise PipelineError("resource_release_unknown")
        if index and not grant_consumed(db, base.task_id):
            raise PipelineError("approval_required")
        target = plan if index == len(history) - 1 else base_plan_for_history(db, history[index + 1])
        amount = target.envelope.budget.max_iteration_cost_usd
        if index and not base.envelope.budget.max_iteration_cost_usd <= amount <= 1:
            raise PipelineError("budget_scope")
        expected_child = base.envelope.budget.model_copy(update={
            "max_iteration_cost_usd": amount, "max_total_cost_usd": amount}) if index else base.envelope.budget
        if target.envelope.budget != expected_child:
            raise PipelineError("budget_scope")
        validate_grant(item, previous_budget, amount, followup=index == 1)
        previous_budget = item.budget_after
    others = db.execute("""SELECT o.operation_id FROM operations o WHERE
        (o.task_id=? OR o.task_id IN (SELECT task_id FROM ui_child_bindings WHERE root_task_id=?))
        AND o.task_id<>?
        AND o.state IN ('prepared','submitting','submitted','running','outcome_unknown')""",
        (root_id, root_id, plan.task_id)).fetchall()
    allowed = {item.base_operation_id for item in history}
    if any(row["operation_id"] not in allowed for row in others):
        raise PipelineError("awaiting_reconciliation")
    if require_consumed and not grant_consumed(db, plan.task_id):
        raise PipelineError("approval_required")
    return binding


def base_plan_for_history(db, binding):
    row = db.execute("SELECT plan FROM tasks WHERE task_id=?", (binding.base_task_id,)).fetchone()
    if row is None:
        raise PipelineError("operation_scope")
    return PipelinePlan.model_validate_json(row["plan"])
