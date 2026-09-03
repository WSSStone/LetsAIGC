from __future__ import annotations

import hashlib
import json

from ..errors import PolicyError
from ..schemas import ApprovalRecord, BudgetUsage, GenerationPlan, TaskBudget


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def plan_fingerprint(plan: GenerationPlan) -> str:
    return hashlib.sha256(canonical_json(plan.model_dump(mode="json"))).hexdigest()


def approve_plan(plan: GenerationPlan, supplied_fingerprint: str) -> ApprovalRecord:
    expected = plan_fingerprint(plan)
    if supplied_fingerprint != expected:
        raise PolicyError(
            "Approval fingerprint does not match the current plan",
            details={"expected": expected, "supplied": supplied_fingerprint},
        )
    return ApprovalRecord(plan_fingerprint=expected, envelope=plan.envelope)


class BudgetLedger:
    def __init__(self, budget: TaskBudget, usage: BudgetUsage | None = None) -> None:
        self.budget = budget
        self.usage = usage or BudgetUsage()

    def reserve(self, *, cost_usd: float = 0, gpu_minutes: float = 0) -> None:
        if cost_usd < 0 or gpu_minutes < 0:
            raise PolicyError("Budget reservation cannot be negative")
        if cost_usd > self.budget.max_iteration_cost_usd:
            raise PolicyError("Iteration cost reservation exceeds budget")
        if gpu_minutes > self.budget.max_iteration_gpu_minutes:
            raise PolicyError("Iteration GPU reservation exceeds budget")
        if self.usage.actual_cost_usd + self.usage.unsettled_cost_usd + cost_usd > self.budget.max_total_cost_usd:
            raise PolicyError("Total cost reservation exceeds budget")
        if (
            self.usage.actual_gpu_minutes + self.usage.unsettled_gpu_minutes + gpu_minutes
            > self.budget.max_total_gpu_minutes
        ):
            raise PolicyError("Total GPU reservation exceeds budget")
        self.usage.reserved_cost_usd = cost_usd
        self.usage.reserved_gpu_minutes = gpu_minutes

    def settle(self, *, cost_usd: float = 0, gpu_minutes: float = 0) -> BudgetUsage:
        if cost_usd < 0 or gpu_minutes < 0:
            raise PolicyError("Budget settlement cannot be negative")
        cost_overrun = cost_usd > self.usage.reserved_cost_usd + 1e-9
        gpu_overrun = gpu_minutes > self.usage.reserved_gpu_minutes + 1e-9
        # An overrun already happened: preserve actual consumption before stopping.
        self.usage.actual_cost_usd += cost_usd
        self.usage.actual_gpu_minutes += gpu_minutes
        self.usage.reserved_cost_usd = 0
        self.usage.reserved_gpu_minutes = 0
        if cost_overrun or gpu_overrun:
            raise PolicyError("Actual cost or GPU time exceeded its reservation (budget overrun)")
        return self.usage

    def release(self) -> None:
        self.usage.reserved_cost_usd = 0
        self.usage.reserved_gpu_minutes = 0

    def abandon(self) -> None:
        """Quarantine uncertain billed work without mislabeling it as measured usage."""
        self.usage.unsettled_cost_usd += self.usage.reserved_cost_usd
        self.usage.unsettled_gpu_minutes += self.usage.reserved_gpu_minutes
        self.release()
