from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from letsaigc.cli import app
from letsaigc.comfy import ComfyClient
from letsaigc.execution.temporal.config import TemporalConfig
from letsaigc.pipelines.comfy import ComfyOperationBackend
from letsaigc.pipelines.contracts import Submission
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas import ExecutionEnvelope, GenerationPlan, RunManifest, TaskBudget
from letsaigc.schemas.pipeline import PipelinePlan


def generation_plan():
    budget = TaskBudget(
        max_total_cost_usd=0,
        max_iteration_cost_usd=0,
        max_total_gpu_minutes=2,
        max_iteration_gpu_minutes=2,
        max_revisions=0,
    )
    return GenerationPlan(
        task_id="task-legacy",
        session_id="session-template",
        intent="text_to_image",
        user_intent="a simple icon",
        backend="comfy",
        model="sd15-fp16",
        recipe="sd15-t2i",
        parameters={"prompt": "a simple icon", "seed": 42},
        acceptance_criteria=["one image"],
        estimated_iteration_gpu_minutes=1,
        envelope=ExecutionEnvelope(
            backend="comfy",
            model="sd15-fp16",
            recipe="sd15-t2i",
            max_width=64,
            max_height=64,
            max_steps=1,
            allowed_tools=["comfy.generate"],
            budget=budget,
        ),
    )


def test_pipeline_plan_cli_and_sdk_optional(monkeypatch, tmp_path):
    monkeypatch.setattr("letsaigc.pipelines.cli.runtime_root", lambda: tmp_path)
    monkeypatch.setattr("letsaigc.pipelines.cli.load_config", TemporalConfig)
    runner = CliRunner()
    result = runner.invoke(app, ["--json", "pipeline", "plan", "--task-id", "cli-smoke"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert PipelinePlan.model_validate(payload["plan"]).fingerprint == payload["fingerprint"]
    assert not list(tmp_path.glob("*.sqlite-journal"))
    monkeypatch.setattr("letsaigc.execution.temporal.config.importlib.util.find_spec", lambda name: None)
    assert runner.invoke(app, ["--help"]).exit_code == 0
    assert runner.invoke(app, ["pipeline", "inspect", "cli-smoke", "--local"]).exit_code == 0
    failed = runner.invoke(app, ["pipeline", "start", "cli-smoke"])
    assert failed.exit_code != 0
    assert getattr(failed.exception, "code", None) == "temporal_unavailable"


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0:7233",
        "other.test:7233",
        "127.0.0.1:7233?token=fake",
        "user:fake@localhost:7233",
    ],
)
def test_temporal_config_rejects_nonlocal_and_sensitive_urls(address):
    with pytest.raises(ValidationError):
        TemporalConfig(address=address)


def test_generation_template_gets_new_executor_identity(tmp_path):
    service = PipelineService(tmp_path)
    source = generation_plan()
    plan = service.generation_plan(source)
    assert plan.task_id != source.task_id
    assert plan.task_id.startswith("pipeline-")
    assert plan == service.generation_plan(source)
    copied = GenerationPlan.model_validate_json(service.artifacts.read(plan.generation_plan))
    assert copied.task_id == plan.task_id
    assert copied.parameters["seed"] == 42
    assert plan.inputs == []
    with pytest.raises(ValueError):
        service.generation_plan(source.model_copy(update={"parameters": {"prompt": "https://fake.test?secret=fake"}}))


def receipt_metadata(**updates):
    return {
        "run_id": "test-run",
        "task_id": "task-test",
        "operation_id": "op-1",
        "graph_sha256": "0" * 64,
        "started_at": time.time(),
        "timeout_seconds": 10,
        "outputs": [],
        **updates,
    }


def test_comfy_submit_preserves_recoverable_receipt(monkeypatch):
    captured = []
    request_id = "00000000-0000-0000-0000-000000000001"

    def post(url, **kwargs):
        captured.append(kwargs["json"])
        return httpx.Response(200, json={"prompt_id": request_id}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    client = ComfyClient()
    assert client.submit({"1": {}}, extra_data={"letsaigc_operation_id": "op-1"}) == request_id
    assert captured[0]["extra_data"]["letsaigc_operation_id"] == "op-1"

    receipt = receipt_metadata()
    monkeypatch.setattr(
        "letsaigc.pipelines.comfy.load_manifest",
        lambda run_id: SimpleNamespace(
            parameters={"agent_task_id": "task-test", "iteration_id": "op-1"},
            source={"compiled_graph_sha256": "0" * 64},
        ),
    )
    prompt = [0, request_id, {}, {"letsaigc_operation_id": "op-1", "letsaigc_receipt": receipt}]
    monkeypatch.setattr(client, "queue", lambda: {"queue_running": [prompt], "queue_pending": []})
    monkeypatch.setattr(client, "_get", lambda path: {})
    backend = ComfyOperationBackend(SimpleNamespace(client=client))
    assert backend.recover("op-1") == Submission(request_id=request_id, metadata=receipt)
    assert backend.recover("missing") is None


def test_comfy_timeout_retains_unknown_and_only_cancels_owned_request(monkeypatch):
    client = ComfyClient()
    request_id = "00000000-0000-0000-0000-000000000002"
    monkeypatch.setattr(client, "history", lambda key: {})
    monkeypatch.setattr(client, "queue", lambda: {"queue_running": [[0, request_id]], "queue_pending": []})
    cancelled = []
    monkeypatch.setattr(client, "cancel_owned_prompt", cancelled.append)
    backend = ComfyOperationBackend(SimpleNamespace(client=client))
    receipt = Submission(
        request_id=request_id,
        metadata=receipt_metadata(
            started_at=time.time() - 10,
            timeout_seconds=1,
        ),
    )
    assert backend.inspect(receipt).state == "unknown"
    assert cancelled == [request_id]
    assert backend.cancel(receipt).state == "unknown"


def test_comfy_completion_collects_real_file_and_preserves_manifest(monkeypatch, tmp_path):
    from PIL import Image

    from letsaigc.tracking.manifest import create_manifest

    output = tmp_path / "output.png"
    Image.new("RGB", (8, 8), "red").save(output)
    manifest = create_manifest(kind="inference", parameters={"seed": 42}, license_lanes=[])
    manifest.parameters.update(agent_task_id="task-test", iteration_id="op-1")
    manifest.source["compiled_graph_sha256"] = "0" * 64
    request_id = "00000000-0000-0000-0000-000000000003"
    record = {
        "outputs": {"1": {"images": [{"filename": output.name, "subfolder": "", "type": "output"}]}},
        "status": {
            "completed": True,
            "messages": [
                ["execution_start", {"timestamp": 1000}],
                ["execution_success", {"timestamp": 2000}],
            ],
        },
    }
    client = ComfyClient()
    monkeypatch.setattr(client, "history", lambda key: {key: record})
    monkeypatch.setattr("letsaigc.pipelines.comfy.local_path", lambda *parts: tmp_path)
    monkeypatch.setattr("letsaigc.pipelines.comfy.load_manifest", lambda run_id: manifest)
    saved = []
    monkeypatch.setattr("letsaigc.pipelines.comfy.save_manifest", saved.append)
    backend = ComfyOperationBackend(SimpleNamespace(client=client))
    receipt = Submission(
        request_id=request_id,
        metadata={
            **receipt_metadata(),
            "run_id": manifest.run_id,
            "started_at": time.time(),
            "timeout_seconds": 5,
            "outputs": [
                {
                    "node_id": "1",
                    "history_field": "images",
                    "role": "primary",
                    "media_kind": "image",
                    "allowed_extensions": [".png"],
                }
            ],
        },
    )
    observed = backend.inspect(receipt)
    assert observed.state == "succeeded"
    assert observed.actual.gpu_minutes == pytest.approx(1 / 60)
    outputs = backend.collect(receipt)
    assert outputs[0] == ("primary-0", output.read_bytes(), "image/png")
    assert backend.collect(receipt) == outputs
    assert len(manifest.outputs) == 1
    assert manifest.status == "succeeded"
    assert RunManifest.model_validate_json(manifest.model_dump_json()).parameters["seed"] == 42


def test_temporal_converter_roundtrips_without_paths(tmp_path):
    pytest.importorskip("temporalio")
    from temporalio.contrib.pydantic import pydantic_data_converter

    async def scenario():
        plan = PipelineService(tmp_path).smoke_plan("roundtrip")
        payloads = await pydantic_data_converter.encode([plan])
        result = await pydantic_data_converter.decode(payloads, [PipelinePlan])
        assert result == [plan]
        assert str(tmp_path).encode() not in payloads[0].data

    asyncio.run(scenario())


def test_windows_model_enum_mapping_keeps_same_approved_model():
    from letsaigc.workflows.compiler import normalize_model_paths

    graph = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd15/model.safetensors"}}}
    info = {
        "CheckpointLoaderSimple": {
            "input": {"required": {"ckpt_name": [["sd15\\model.safetensors", "other\\model.safetensors"]]}}
        }
    }
    normalize_model_paths(graph, info)
    assert graph["1"]["inputs"]["ckpt_name"] == "sd15\\model.safetensors"
    graph["1"]["inputs"]["ckpt_name"] = "unapproved.safetensors"
    normalize_model_paths(graph, info)
    assert graph["1"]["inputs"]["ckpt_name"] == "unapproved.safetensors"


def test_standalone_worker_can_cold_import_comfy_backend():
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    result = subprocess.run(
        [sys.executable, "-c", "from letsaigc.pipelines.comfy import ComfyOperationBackend"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
