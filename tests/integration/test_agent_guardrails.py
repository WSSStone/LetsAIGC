from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from letsaigc.agent import AgentOrchestrator, AgentStore
from letsaigc.agent.approval import plan_fingerprint
from letsaigc.backends.base import GenerationResult
from letsaigc.errors import PolicyError
from letsaigc.generation.router import default_capabilities
from letsaigc.schemas import AgentEvaluation, BackendName, LicenseLane, TaskBudget
from letsaigc.tracking import load_manifest, save_manifest
from letsaigc.tracking.manifest import add_output, create_manifest


class Model:
    def __init__(self, intent="text_to_image", parameters=None, accept=True):
        self.intent, self.parameters, self.accept = intent, parameters or {}, accept

    def interpret(self, intent, assets, capabilities):
        return {"intent": self.intent, "prompt": intent, "parameters": self.parameters,
                "negative_prompt": "text", "acceptance_criteria": ["matches intent"]}

    def evaluate(self, intent, outputs, hard):
        return AgentEvaluation(hard_constraints=hard, score=0.9 if self.accept else 0.5,
                               revision={} if self.accept else {"seed": 123})


class Backend:
    def __init__(self, root):
        self.root, self.calls = root, 0

    def execute(self, plan, *, iteration_id):
        self.calls += 1
        path = self.root / f"{iteration_id}.png"
        Image.new("RGB", (1024, 1024), "red").save(path)
        return GenerationResult(outputs=[path], model_snapshot=plan.model, request_id=f"req-{iteration_id}")


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    repo = Path(__file__).parents[2]
    (tmp_path / "configs/providers").mkdir(parents=True)
    for name in ("openai-pricing.yaml", "openai-models.yaml"):
        (tmp_path / "configs/providers" / name).write_bytes((repo / "configs/providers" / name).read_bytes())
    monkeypatch.setenv("LETSAIGC_ROOT", str(tmp_path))
    monkeypatch.setattr("letsaigc.agent.orchestrator.log_manifest", lambda *args, **kwargs: None)
    return tmp_path


def budget(revisions=0):
    return TaskBudget(max_total_cost_usd=2, max_iteration_cost_usd=0.3,
                      max_total_gpu_minutes=180, max_iteration_gpu_minutes=15, max_revisions=revisions)


def setup_agent(workspace, model=None, capabilities=None):
    backend = Backend(workspace)
    agent = AgentOrchestrator(store=AgentStore(workspace / ".local/agent"), model=model or Model(),
                              capabilities=capabilities or default_capabilities(),
                              backends={"comfy.generate": backend, "openai.image": backend})
    return agent, backend


def plan(agent, revisions=0):
    return agent.plan("potion", image_sources=[], requested_backend=BackendName.auto, budget=budget(revisions))


def test_remote_fallback_is_only_a_plan_until_exact_approval(workspace):
    capabilities = default_capabilities()
    capabilities[0].available = False
    agent, backend = setup_agent(workspace, capabilities=capabilities)
    pending = plan(agent)
    assert pending["plan"]["backend"] == "openai"
    assert pending["state"] == "awaiting_approval" and backend.calls == 0
    with pytest.raises(PolicyError, match="fingerprint"):
        agent.execute(pending["id"], approval_fingerprint="0" * 64)
    assert backend.calls == 0
    result = agent.execute(pending["id"], approval_fingerprint=pending["plan_fingerprint"])
    assert result["stop_reason"] == "accepted"
    child = load_manifest(result["final_candidate"]["run_id"])
    assert child.kind == "remote_image_generation" and child.agent.provider_request_id
    assert child.parent_run_id == result["run_id"] and len(child.outputs[0].sha256) == 64


def test_ten_revisions_mean_eleven_generations_and_no_more(workspace):
    agent, backend = setup_agent(workspace, Model(accept=False))
    pending = plan(agent, revisions=10)
    result = agent.execute(pending["id"], approval_fingerprint=pending["plan_fingerprint"])
    assert backend.calls == 11
    assert result["stop_reason"] == "revision_limit"
    assert result["final_candidate"] is not None


def test_changed_config_and_forbidden_tool_fail_before_dispatch(workspace):
    agent, backend = setup_agent(workspace)
    pending = plan(agent)
    pricing = workspace / "configs/providers/openai-pricing.yaml"
    pricing.write_text(pricing.read_text() + "\n# changed\n")
    with pytest.raises(PolicyError, match="SHA-256"):
        agent.execute(pending["id"], approval_fingerprint=pending["plan_fingerprint"])
    task = agent.store.load_task(pending["id"])
    task.plan.dependency_hashes = {}
    task.plan.envelope.allowed_tools = ["shell"]
    agent.store.save_task(task)
    with pytest.raises(PolicyError, match="prohibited"):
        agent.execute(task.id, approval_fingerprint=plan_fingerprint(task.plan))
    assert backend.calls == 0


def test_rejection_and_reentrant_execution_do_not_generate(workspace):
    agent, backend = setup_agent(workspace)
    pending = plan(agent)
    with agent.store.execution_lock(pending["id"]):
        with pytest.raises(PolicyError, match="locked"):
            agent.execute(pending["id"], approval_fingerprint=pending["plan_fingerprint"])
    assert agent.reject(pending["id"])["stop_reason"] == "user_rejected"
    assert backend.calls == 0


@pytest.mark.parametrize("intent,role", [("sprite_sequence", "sprite_sheet"), ("short_drama", "drama_video")])
def test_agent_media_tools_preserve_upstream_and_agent_lineage(workspace, monkeypatch, intent, role):
    path = workspace / "source.png"
    Image.new("RGB", (32, 32), "green").save(path)
    source = create_manifest(kind="video_generation", parameters={}, license_lanes=[LicenseLane.production])
    add_output(source, path, role="primary_video")
    source.status = "succeeded"
    save_manifest(source)
    if intent == "sprite_sequence":
        config = workspace / "configs/sprites/test.yaml"
        config.parent.mkdir(parents=True)
        config.write_text("id: test\n")
        parameters = {"source_run_id": source.run_id, "profile": "configs/sprites/test.yaml"}
    else:
        config = workspace / "configs/drama/test.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"schema_version": 1, "id": "test", "width": 32, "height": 32, "fps": 12,
                                      "shots": [{"id": "one", "source_run_id": source.run_id}]}))
        parameters = {"project": "configs/drama/test.yaml"}

    def fake_media(*args, **kwargs):
        result = create_manifest(kind="sprite_pipeline" if intent == "sprite_sequence" else "drama_render",
                                 parameters={}, license_lanes=[LicenseLane.production], parent_run_id=source.run_id)
        add_output(result, path, role=role, derived_from_run_id=source.run_id)
        result.governance.validations = {"contract": True, "hashes": True, "provenance": True}
        result.status = "succeeded"
        save_manifest(result)
        return json.loads(result.model_dump_json())

    monkeypatch.setattr("letsaigc.backends.media.build_sprite_sequence", fake_media)
    monkeypatch.setattr("letsaigc.backends.media.render_drama", fake_media)
    agent = AgentOrchestrator(model=Model(intent, parameters), capabilities=default_capabilities())
    pending = plan(agent)
    result = agent.execute(pending["id"], approval_fingerprint=pending["plan_fingerprint"])
    assert result["stop_reason"] == "accepted"
    child = load_manifest(result["final_candidate"]["run_id"])
    assert child.parent_run_id == result["run_id"]
    assert child.source["derived_from_run_id"] == source.run_id
    assert child.outputs[0].derived_from_run_id == source.run_id
