from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from ..agent.approval import canonical_json
from ..agent.storage import AgentStore
from ..config import load_typed
from ..errors import ReadinessError, ValidationError
from ..paths import find_repo_root
from ..schemas import CompiledWorkflow, GenerationIntent, GenerationPlan, WorkflowRecipe

NATIVE_NODES = {
    "CheckpointLoaderSimple",
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "LoadImage",
    "EmptyLatentImage",
    "EmptyHunyuanLatentVideo",
    "Wan22ImageToVideoLatent",
    "VAEEncode",
    "VAEDecode",
    "CLIPTextEncode",
    "KSampler",
    "CreateVideo",
    "SaveImage",
    "SaveVideo",
}


def load_recipe(recipe_id: str) -> WorkflowRecipe:
    if not recipe_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-." for char in recipe_id):
        raise ValidationError("Invalid workflow recipe id")
    path = find_repo_root() / "configs" / "workflows" / "recipes" / f"{recipe_id}.yaml"
    if not path.is_file():
        raise ReadinessError(f"Unknown workflow recipe: {recipe_id}")
    return load_typed(path, WorkflowRecipe)


def normalize_model_paths(graph: dict, object_info: dict | None) -> None:
    """Use the server's enum spelling for the same approved relative model path."""
    if object_info is None:
        return
    fields = {
        "CheckpointLoaderSimple": "ckpt_name",
        "UNETLoader": "unet_name",
        "CLIPLoader": "clip_name",
        "VAELoader": "vae_name",
    }
    for node in graph.values():
        kind = node.get("class_type")
        field = fields.get(kind)
        value = node.get("inputs", {}).get(field)
        specification = object_info.get(kind, {}).get("input", {}).get("required", {}).get(field, [])
        choices = specification[0] if specification and isinstance(specification[0], list) else []
        if not isinstance(value, str) or value in choices:
            continue
        matches = [
            choice
            for choice in choices
            if isinstance(choice, str) and choice.replace("\\", "/") == value.replace("\\", "/")
        ]
        if len(matches) == 1:
            node["inputs"][field] = matches[0]


class WorkflowCompiler:
    def __init__(self, store: AgentStore | None = None) -> None:
        self.store = store or AgentStore()

    def compile(
        self,
        plan: GenerationPlan,
        *,
        uploaded_images: list[str] | None = None,
        object_info: dict[str, Any] | None = None,
    ) -> CompiledWorkflow:
        if plan.backend != "comfy" or not plan.recipe:
            raise ValidationError("ComfyUI compilation requires an approved recipe")
        recipe = load_recipe(plan.recipe)
        if recipe.intent != plan.intent or recipe.id != plan.envelope.recipe:
            raise ValidationError("Recipe intent does not match the approved plan")
        if plan.model not in recipe.models:
            raise ValidationError("Recipe model does not match the approved plan")
        if not set(plan.envelope.mutable_parameters).issubset(set(recipe.mutable_parameters)):
            raise ValidationError("Execution envelope exposes tunables not declared by the recipe")
        if not set(recipe.allowed_nodes).issubset(NATIVE_NODES):
            raise ValidationError("Recipe contains a node outside the native allowlist")
        base = (find_repo_root() / recipe.base_workflow).resolve()
        if find_repo_root().resolve() not in base.parents:
            raise ValidationError("Recipe block is outside the repository")
        if not base.is_file():
            raise ReadinessError(f"Recipe workflow block is missing: {recipe.base_workflow}")
        graph = json.loads(base.read_text(encoding="utf-8"))
        if recipe.intent == GenerationIntent.image_to_image:
            graph = self._compile_sdxl_img2img(graph, uploaded_images)
        elif recipe.intent == GenerationIntent.image_to_video:
            if not uploaded_images:
                raise ValidationError("Image-to-video requires an uploaded input")
            graph["4"]["inputs"]["image"] = uploaded_images[0]
        self._validate_envelope(plan)
        if "frames" in plan.parameters and (plan.parameters["frames"] - 1) % 4:
            raise ValidationError("Wan frame count must be 4n+1")
        self._apply_parameters(graph, recipe, plan.parameters)
        self._scope_outputs(graph, plan)
        normalize_model_paths(graph, object_info)
        self._validate_nodes(graph, recipe, object_info)
        digest = hashlib.sha256(canonical_json(graph)).hexdigest()
        destination = self.store.compiled_dir(plan.session_id, plan.task_id) / f"{digest}.json"
        destination.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        contract = {
            "recipe_id": recipe.id,
            "recipe_version": recipe.version,
            "graph_sha256": digest,
            "models": recipe.models,
            "outputs": recipe.outputs,
            "resource_budget": recipe.resource_budget,
        }
        contract_digest = hashlib.sha256(canonical_json(contract)).hexdigest()
        contract_path = destination.with_name(f"{digest}.contract.json")
        contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
        return CompiledWorkflow(
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            graph=graph,
            graph_sha256=digest,
            models=recipe.models,
            outputs=recipe.outputs,
            local_graph_path=str(destination.resolve()),
            local_contract_path=str(contract_path.resolve()),
            contract_sha256=contract_digest,
            validation={"recipe": True, "native_nodes": True, "object_info": object_info is not None},
        )

    @staticmethod
    def _validate_envelope(plan: GenerationPlan) -> None:
        comparisons = (
            ("width", plan.envelope.max_width),
            ("height", plan.envelope.max_height),
            ("steps", plan.envelope.max_steps),
        )
        for name, maximum in comparisons:
            value = plan.parameters.get(name)
            if maximum is not None and value is not None and value > maximum:
                raise ValidationError(f"Parameter {name} exceeds the approved execution envelope")

    @staticmethod
    def _compile_sdxl_img2img(graph: dict[str, Any], uploaded: list[str] | None) -> dict[str, Any]:
        if not uploaded:
            raise ValidationError("SDXL image-to-image requires an uploaded input")
        result = copy.deepcopy(graph)
        result.pop("5", None)
        result["10"] = {"class_type": "LoadImage", "inputs": {"image": uploaded[0]}}
        result["11"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["10", 0], "vae": ["4", 2]},
        }
        result["3"]["inputs"]["latent_image"] = ["11", 0]
        return result

    @staticmethod
    def _apply_parameters(graph: dict[str, Any], recipe: WorkflowRecipe, parameters: dict[str, Any]) -> None:
        unknown = sorted(set(parameters) - set(recipe.parameter_bounds))
        if unknown:
            raise ValidationError("Parameters are not declared by the recipe", details={"unknown": unknown})
        for name, value in parameters.items():
            rule = recipe.parameter_bounds[name]
            WorkflowCompiler._validate_parameter(name, value, rule)
            node = str(rule["node"])
            input_name = str(rule["input"])
            if node not in graph or input_name not in graph[node].get("inputs", {}):
                raise ValidationError(f"Recipe binding is invalid: {name}")
            graph[node]["inputs"][input_name] = value

    @staticmethod
    def _validate_parameter(name: str, value: Any, rule: dict[str, Any]) -> None:
        kind = rule.get("type")
        if kind == "string":
            if not isinstance(value, str):
                raise ValidationError(f"Recipe parameter {name} must be a string")
            if len(value) < int(rule.get("min_length", 0)) or len(value) > int(rule.get("max_length", 1_000_000)):
                raise ValidationError(f"Recipe parameter {name} is outside length bounds")
            return
        if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValidationError(f"Recipe parameter {name} must be an integer")
        if kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValidationError(f"Recipe parameter {name} must be numeric")
        if kind in {"integer", "number"}:
            if "minimum" in rule and value < rule["minimum"]:
                raise ValidationError(f"Recipe parameter {name} is below its minimum")
            if "maximum" in rule and value > rule["maximum"]:
                raise ValidationError(f"Recipe parameter {name} exceeds its maximum")
            multiple = rule.get("multiple_of")
            if multiple and value % multiple:
                raise ValidationError(f"Recipe parameter {name} violates its multiple")

    @staticmethod
    def _scope_outputs(graph: dict[str, Any], plan: GenerationPlan) -> None:
        prefix = f"letsaigc-agent/{plan.session_id}/{plan.task_id}"
        for node in graph.values():
            if node.get("class_type") in {"SaveImage", "SaveVideo"}:
                node["inputs"]["filename_prefix"] = prefix

    @staticmethod
    def _validate_nodes(graph: dict[str, Any], recipe: WorkflowRecipe, object_info: dict[str, Any] | None) -> None:
        graph_nodes = {node.get("class_type") for node in graph.values()}
        if None in graph_nodes or not graph_nodes.issubset(set(recipe.allowed_nodes)):
            raise ValidationError("Compiled graph contains undeclared nodes")
        if object_info is None:
            return
        for class_type in graph_nodes:
            if class_type not in object_info:
                raise ReadinessError(f"ComfyUI is missing required node: {class_type}")
            module = str(object_info[class_type].get("python_module", ""))
            if "custom_nodes" in module.replace("\\", "/"):
                raise ValidationError(f"Custom ComfyUI node is forbidden: {class_type}")
            input_info = object_info[class_type].get("input", {})
            allowed_inputs = (
                set(input_info.get("required", {}))
                | set(input_info.get("optional", {}))
                | set(input_info.get("hidden", {}))
            )
            if allowed_inputs:
                for node in graph.values():
                    if node.get("class_type") == class_type:
                        unknown = set(node.get("inputs", {})) - allowed_inputs
                        if unknown:
                            raise ReadinessError(
                                f"ComfyUI node input mismatch: {class_type}",
                                details={"unknown_inputs": sorted(unknown)},
                            )
                        missing = set(input_info.get("required", {})) - set(node.get("inputs", {}))
                        if missing:
                            raise ReadinessError(f"ComfyUI required inputs missing: {class_type}: {sorted(missing)}")
                        WorkflowCompiler._validate_input_types(node, graph, input_info, object_info)

    @staticmethod
    def _validate_input_types(
        node: dict[str, Any],
        graph: dict[str, Any],
        input_info: dict[str, Any],
        object_info: dict[str, Any],
    ) -> None:
        specifications = {
            **input_info.get("required", {}),
            **input_info.get("optional", {}),
            **input_info.get("hidden", {}),
        }
        for name, value in node.get("inputs", {}).items():
            specification = specifications.get(name)
            if not isinstance(specification, (list, tuple)) or not specification:
                continue
            expected = specification[0]
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
                source = graph.get(value[0])
                if source is None:
                    raise ReadinessError(f"Compiled graph input {name} references a missing node")
                outputs = object_info[source["class_type"]].get("output", [])
                if value[1] < 0 or value[1] >= len(outputs):
                    raise ReadinessError(f"Compiled graph input {name} references a missing output port")
                if isinstance(expected, str) and outputs[value[1]] != expected:
                    raise ReadinessError(
                        f"ComfyUI port type mismatch for {name}",
                        details={"expected": expected, "actual": outputs[value[1]]},
                    )
                continue
            if isinstance(expected, list):
                if value not in expected:
                    raise ReadinessError(f"ComfyUI enum input mismatch for {name}")
                continue
            python_type = {"INT": int, "FLOAT": (int, float), "STRING": str, "BOOLEAN": bool}.get(expected)
            wrong_type = python_type and not isinstance(value, python_type)
            numeric_boolean = expected in {"INT", "FLOAT"} and isinstance(value, bool)
            if wrong_type or numeric_boolean:
                raise ReadinessError(f"ComfyUI constant input type mismatch for {name}")
