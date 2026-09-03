from __future__ import annotations

from pathlib import Path

from PIL import Image

from letsaigc.agent import AgentOrchestrator, AgentStore
from letsaigc.agent.approval import plan_fingerprint
from letsaigc.backends.base import GenerationResult
from letsaigc.schemas import AgentEvaluation, BackendCapability, BackendName, GenerationIntent, TaskBudget


class FakeModel:
    def __init__(self) -> None:
        self.evaluations = 0

    def interpret(self, intent, assets, capabilities):
        return {
            "intent": "text_to_image",
            "prompt": intent,
            "negative_prompt": "text",
            "acceptance_criteria": ["centered"],
        }

    def evaluate(self, intent, output_paths, hard_constraints):
        self.evaluations += 1
        score = [0.5, 0.7, 0.9][self.evaluations - 1]
        return AgentEvaluation(
            hard_constraints=hard_constraints,
            score=score,
            issues=[] if score >= 0.8 else ["needs refinement"],
            revision={} if score >= 0.8 else {"seed": self.evaluations + 10},
        )


class FakeBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = 0

    def execute(self, plan, *, iteration_id):
        self.calls += 1
        output = self.root / plan.task_id / iteration_id / "candidate.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1024, 1024), (self.calls * 30, 0, 0)).save(output)
        return GenerationResult(outputs=[output])


def test_two_failed_critiques_then_third_candidate_is_accepted(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    monkeypatch.setenv("LETSAIGC_ROOT", str(tmp_path))
    model = FakeModel()
    backend = FakeBackend(tmp_path / "outputs")
    capability = BackendCapability(
        backend="comfy",
        intents=[GenerationIntent.text_to_image],
        media_kinds=["image"],
        supports_alpha=False,
        cost_type="local_gpu",
    )
    orchestrator = AgentOrchestrator(
        store=AgentStore(tmp_path / ".local" / "agent"),
        model=model,
        capabilities=[capability],
        backends={"comfy.generate": backend},
    )
    budget = TaskBudget(
        max_total_cost_usd=0.1,
        max_iteration_cost_usd=0.05,
        max_total_gpu_minutes=45,
        max_iteration_gpu_minutes=15,
        max_revisions=2,
    )
    planned = orchestrator.plan(
        "potion icon",
        image_sources=[],
        requested_backend=BackendName.auto,
        budget=budget,
    )
    task = orchestrator.store.load_task(planned["id"])
    result = orchestrator.execute(task.id, approval_fingerprint=plan_fingerprint(task.plan))
    assert result["stop_reason"] == "accepted"
    assert len(result["iterations"]) == 3
    assert result["final_candidate"]["evaluation"]["score"] == 0.9
    assert backend.calls == 3
    inspected = orchestrator.inspect(task.id)
    assert inspected["state"] == "finalize"
    assert inspected["state_history"][-3:] == ["evaluate", "accepted", "finalize"]
