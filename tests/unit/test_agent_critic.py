from __future__ import annotations

import pytest
from PIL import Image

from letsaigc.agent.critic import AgentCritic, validate_revision
from letsaigc.errors import ValidationError
from letsaigc.schemas import AgentEvaluation, ExecutionEnvelope, GenerationIntent, GenerationPlan, TaskBudget


def _plan() -> GenerationPlan:
    budget = TaskBudget(
        max_total_cost_usd=0.1,
        max_iteration_cost_usd=0.05,
        max_total_gpu_minutes=30,
        max_iteration_gpu_minutes=15,
        max_revisions=1,
    )
    return GenerationPlan(
        task_id="task-critic",
        session_id="session-critic",
        intent=GenerationIntent.text_to_image,
        user_intent="icon",
        backend="comfy",
        model="sdxl-base-1.0",
        recipe="sdxl-t2i",
        parameters={
            "prompt": "icon",
            "negative_prompt": "text",
            "seed": 1,
            "steps": 20,
            "cfg": 6,
            "width": 1024,
            "height": 1024,
        },
        acceptance_criteria=["centered"],
        envelope=ExecutionEnvelope(
            backend="comfy",
            model="sdxl-base-1.0",
            recipe="sdxl-t2i",
            max_width=1536,
            max_height=1536,
            max_steps=40,
            allowed_tools=["comfy.generate"],
            mutable_parameters=["prompt", "negative_prompt", "seed", "steps", "cfg"],
            budget=budget,
        ),
    )


def test_revision_is_limited_to_approved_mutable_parameters() -> None:
    plan = _plan()
    assert validate_revision(plan, {"seed": 2})["seed"] == 2
    with pytest.raises(ValidationError, match="non-approved"):
        validate_revision(plan, {"width": 2048})


def test_critic_cannot_override_deterministic_constraint(tmp_path) -> None:
    class UnsafeCritic:
        def evaluate(self, intent, paths, hard):
            return AgentEvaluation(hard_constraints={"dimensions": True}, score=1)

    path = tmp_path / "wide.png"
    Image.new("RGB", (1600, 8), "red").save(path)
    evaluation = AgentCritic(UnsafeCritic()).evaluate(_plan(), [path], iteration_id="iter-00")
    assert evaluation.hard_constraints["dimensions"] is False
    assert not evaluation.accepted
