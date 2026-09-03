from __future__ import annotations

from pathlib import Path

import pytest

from letsaigc.agent.storage import AgentStore
from letsaigc.errors import ReadinessError, ValidationError
from letsaigc.schemas import (
    ExecutionEnvelope,
    GenerationIntent,
    GenerationPlan,
    TaskBudget,
)
from letsaigc.workflows.compiler import WorkflowCompiler


def _budget() -> TaskBudget:
    return TaskBudget(
        max_total_cost_usd=0.1,
        max_iteration_cost_usd=0.05,
        max_total_gpu_minutes=60,
        max_iteration_gpu_minutes=15,
        max_revisions=2,
    )


def _i2i_plan(parameters: dict | None = None) -> GenerationPlan:
    return GenerationPlan(
        task_id="task-compiler",
        session_id="session-compiler",
        intent=GenerationIntent.image_to_image,
        user_intent="restyle image",
        backend="comfy",
        model="sdxl-base-1.0",
        recipe="sdxl-i2i",
        parameters=parameters
        or {"prompt": "watercolor potion", "negative_prompt": "text", "seed": 1, "steps": 20, "cfg": 6, "denoise": 0.6},
        acceptance_criteria=["matches input silhouette"],
        envelope=ExecutionEnvelope(
            backend="comfy",
            model="sdxl-base-1.0",
            recipe="sdxl-i2i",
            max_width=1536,
            max_height=1536,
            max_steps=40,
            allowed_tools=["comfy.generate"],
            mutable_parameters=["denoise"],
            budget=_budget(),
        ),
    )


def test_sdxl_i2i_compiles_to_load_and_vae_encode(tmp_path: Path) -> None:
    compiled = WorkflowCompiler(AgentStore(tmp_path / "agent")).compile(
        _i2i_plan(), uploaded_images=["letsaigc-agent/session/input.png"]
    )
    classes = {node["class_type"] for node in compiled.graph.values()}
    assert "LoadImage" in classes
    assert "VAEEncode" in classes
    assert "EmptyLatentImage" not in classes
    assert compiled.graph["3"]["inputs"]["latent_image"] == ["11", 0]
    assert Path(compiled.local_graph_path).is_file()
    assert Path(compiled.local_contract_path).is_file()
    assert len(compiled.contract_sha256) == 64


def test_recipe_rejects_undeclared_parameter(tmp_path: Path) -> None:
    parameters = _i2i_plan().parameters | {"custom_node": True}
    with pytest.raises(ValidationError, match="not declared"):
        WorkflowCompiler(AgentStore(tmp_path / "agent")).compile(
            _i2i_plan(parameters), uploaded_images=["safe/input.png"]
        )


def test_object_info_missing_node_fails_before_prompt(tmp_path: Path) -> None:
    with pytest.raises(ReadinessError, match="missing required node"):
        WorkflowCompiler(AgentStore(tmp_path / "agent")).compile(
            _i2i_plan(),
            uploaded_images=["safe/input.png"],
            object_info={},
        )


def test_object_info_custom_node_module_is_rejected(tmp_path: Path) -> None:
    compiler = WorkflowCompiler(AgentStore(tmp_path / "agent"))
    initial = compiler.compile(_i2i_plan(), uploaded_images=["safe/input.png"])
    info = {}
    for node in initial.graph.values():
        class_type = node["class_type"]
        info[class_type] = {
            "python_module": "custom_nodes.bad" if class_type == "KSampler" else "nodes",
            "input": {"required": {name: [] for name in node["inputs"]}},
        }
    with pytest.raises(ValidationError, match="Custom ComfyUI node"):
        compiler.compile(_i2i_plan(), uploaded_images=["safe/input.png"], object_info=info)


def test_enum_inputs_are_supported_and_ports_are_type_checked() -> None:
    graph = {"1": {"class_type": "Source", "inputs": {}}}
    WorkflowCompiler._validate_input_types(
        {"inputs": {"sampler": "euler"}}, graph,
        {"required": {"sampler": [["euler", "uni_pc"]]}}, {},
    )
    with pytest.raises(ReadinessError, match="type mismatch"):
        WorkflowCompiler._validate_input_types(
            {"inputs": {"latent": ["1", 0]}}, graph,
            {"required": {"latent": ["LATENT"]}}, {"Source": {"output": ["IMAGE"]}},
        )


@pytest.mark.parametrize("recipe,intent,node", [
    ("wan21-t2v", GenerationIntent.text_to_video, "EmptyHunyuanLatentVideo"),
    ("wan22-i2v-experimental", GenerationIntent.image_to_video, "Wan22ImageToVideoLatent"),
])
def test_wan_recipes_compile_with_correct_latent_kind(tmp_path, recipe, intent, node) -> None:
    plan = _i2i_plan().model_copy(deep=True)
    plan.recipe = plan.envelope.recipe = recipe
    plan.model = plan.envelope.model = "wan21-t2v-1.3b" if recipe == "wan21-t2v" else "wan22-ti2v-5b"
    plan.intent = intent
    plan.envelope.mutable_parameters = ["prompt", "seed"]
    plan.parameters = {"prompt": "slime", "seed": 7, "frames": 17, "width": 512, "height": 512, "steps": 20}
    compiler = WorkflowCompiler(AgentStore(tmp_path / "agent"))
    result = compiler.compile(plan, uploaded_images=["safe/input.png"])
    assert node in {item["class_type"] for item in result.graph.values()}
    assert compiler.compile(plan, uploaded_images=["safe/input.png"]).graph_sha256 == result.graph_sha256
    plan.parameters["frames"] = 16
    with pytest.raises(ValidationError, match="4n"):
        compiler.compile(plan, uploaded_images=["safe/input.png"])
