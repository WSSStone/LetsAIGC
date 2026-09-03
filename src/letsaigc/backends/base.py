from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..schemas import GenerationPlan


@dataclass(slots=True)
class GenerationResult:
    outputs: list[Path]
    run_id: str | None = None
    request_id: str | None = None
    model_snapshot: str | None = None
    usage: dict = field(default_factory=dict)
    actual_cost_usd: float = 0
    actual_gpu_minutes: float = 0
    request_hash: str | None = None


class GenerationBackend(Protocol):
    name: str

    def execute(self, plan: GenerationPlan, *, iteration_id: str) -> GenerationResult: ...
