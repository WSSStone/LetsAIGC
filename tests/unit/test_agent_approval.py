from __future__ import annotations

import pytest

from letsaigc.agent.approval import BudgetLedger, approve_plan, plan_fingerprint
from letsaigc.errors import PolicyError
from letsaigc.schemas import ExecutionEnvelope, GenerationIntent, GenerationPlan, TaskBudget


def _plan() -> GenerationPlan:
    budget = TaskBudget(
        max_total_cost_usd=1,
        max_iteration_cost_usd=0.25,
        max_total_gpu_minutes=0,
        max_iteration_gpu_minutes=0,
        max_revisions=2,
    )
    return GenerationPlan(
        task_id="task-1",
        session_id="session-1",
        intent=GenerationIntent.text_to_image,
        user_intent="a potion icon",
        backend="openai",
        model="gpt-image-2-2026-04-21",
        parameters={"prompt": "a potion icon"},
        acceptance_criteria=["potion is centered"],
        envelope=ExecutionEnvelope(
            backend="openai",
            model="gpt-image-2-2026-04-21",
            max_width=1024,
            max_height=1024,
            quality="low",
            background="auto",
            allowed_tools=["openai.image"],
            mutable_parameters=["prompt"],
            budget=budget,
        ),
    )


def test_approval_requires_exact_plan_fingerprint() -> None:
    plan = _plan()
    fingerprint = plan_fingerprint(plan)
    assert approve_plan(plan, fingerprint).plan_fingerprint == fingerprint
    changed = plan.model_copy(update={"parameters": {"prompt": "larger potion"}}, deep=True)
    with pytest.raises(PolicyError, match="does not match"):
        approve_plan(changed, fingerprint)


def test_budget_ledger_fails_closed() -> None:
    ledger = BudgetLedger(_plan().envelope.budget)
    ledger.reserve(cost_usd=0.2)
    ledger.settle(cost_usd=0.1)
    with pytest.raises(PolicyError, match="Iteration"):
        ledger.reserve(cost_usd=0.3)
    ledger.reserve(cost_usd=0.2)
    with pytest.raises(PolicyError, match="exceeded its reservation"):
        ledger.settle(cost_usd=0.21)
    assert ledger.usage.actual_cost_usd == pytest.approx(0.31)


def test_uncertain_cost_remains_reserved_against_total() -> None:
    budget = _plan().envelope.budget.model_copy(update={"max_total_cost_usd": 0.3})
    ledger = BudgetLedger(budget)
    ledger.reserve(cost_usd=0.2)
    ledger.abandon()
    assert ledger.usage.actual_cost_usd == 0
    assert ledger.usage.unsettled_cost_usd == 0.2
    with pytest.raises(PolicyError, match="Total"):
        ledger.reserve(cost_usd=0.2)
