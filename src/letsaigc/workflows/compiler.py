from __future__ import annotations

import copy
import hashlib
import io
import json
import math
from collections.abc import Mapping
from typing import Any

from PIL import Image

from ..agent.approval import canonical_json
from ..agent.storage import AgentStore
from ..config import load_typed, load_workflow_contract
from ..errors import ReadinessError, ValidationError
from ..paths import find_repo_root
from ..pipelines.errors import PipelineError
from ..schemas import CompiledWorkflow, GenerationIntent, GenerationPlan, WorkflowRecipe
from ..schemas.agent import MaskedGenerationPlan
from ..ui_analysis.inpaint import prepare_edit_mask, resize_and_pad_image
from ..ui_analysis.selection import (
    TrustedSource,
    _covered_by_union,
    _layout_elements,
    _load_layout,
    validate_selection,
)

NATIVE_NODES = {
    "CheckpointLoaderSimple",
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "LoadImage",
    "ImageToMask",
    "EmptyLatentImage",
    "EmptyHunyuanLatentVideo",
    "Wan22ImageToVideoLatent",
    "VAEEncode",
    "VAEEncodeForInpaint",
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
        uploaded_mask: str | None = None,
        trusted_artifacts: Any | None = None,
        trusted_sources: Any | None = None,
        object_info: dict[str, Any] | None = None,
    ) -> CompiledWorkflow:
        if isinstance(plan, MaskedGenerationPlan):
            return self._compile_masked(
                plan,
                uploaded_images=uploaded_images,
                uploaded_mask=uploaded_mask,
                trusted_artifacts=trusted_artifacts,
                trusted_sources=trusted_sources,
                object_info=object_info,
            )
        if uploaded_mask is not None or trusted_artifacts is not None:
            raise ValidationError("Masked compiler arguments require a MaskedGenerationPlan v2")
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

    def _compile_masked(
        self,
        plan: MaskedGenerationPlan,
        *,
        uploaded_images: list[str] | None,
        uploaded_mask: str | None,
        trusted_artifacts: Any | None,
        trusted_sources: Any | None,
        object_info: dict[str, Any] | None,
    ) -> CompiledWorkflow:
        """Compile a v2 masked plan before the ordinary image-to-image path."""

        if trusted_artifacts is None or not callable(getattr(trusted_artifacts, "read", None)):
            raise ValidationError("Masked inpaint compilation requires a trusted artifact store")
        recipe = self._validate_masked_plan_metadata(plan)
        artifacts = self._read_masked_artifacts(plan, trusted_artifacts, trusted_sources)
        empty_mask = artifacts["canonical_mask"].getbbox() is None
        if empty_mask:
            graph: dict[str, Any] = {}
        else:
            if not uploaded_images or len(uploaded_images) != 1 or not uploaded_mask:
                raise ValidationError("Masked inpaint compilation requires one paired image and mask upload")
            self._validate_upload_handle(uploaded_images[0], "image")
            self._validate_upload_handle(uploaded_mask, "mask")
            base = (find_repo_root() / recipe.base_workflow).resolve()
            if find_repo_root().resolve() not in base.parents:
                raise ValidationError("Recipe workflow block is outside the repository")
            if not base.is_file():
                raise ReadinessError(f"Recipe workflow block is missing: {recipe.base_workflow}")
            graph = self._compile_sdxl_inpaint(
                json.loads(base.read_text(encoding="utf-8")), uploaded_images[0], uploaded_mask
            )
            if "frames" in plan.parameters and (plan.parameters["frames"] - 1) % 4:
                raise ValidationError("Wan frame count must be 4n+1")
            self._apply_parameters(graph, recipe, plan.parameters)
            self._scope_outputs(graph, plan)
            normalize_model_paths(graph, object_info)
            self._validate_nodes(graph, recipe, object_info)

        graph_digest = hashlib.sha256(canonical_json(graph)).hexdigest()
        destination = self.store.compiled_dir(plan.session_id, plan.task_id) / f"{graph_digest}.json"
        destination.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        mask_binding = plan.image_mask
        evidence = {
            "recipe_id": recipe.id,
            "recipe_version": recipe.version,
            "graph_sha256": graph_digest,
            "models": recipe.models,
            "outputs": recipe.outputs,
            "resource_budget": recipe.resource_budget,
            "masked_plan_schema_version": plan.schema_version,
            "frozen_parameters": {
                name: plan.parameters[name] for name in ("steps", "cfg", "denoise")
            },
            "no_edit_pixels": empty_mask,
            "mask": {
                "channel": "red",
                "polarity": "white_is_edit",
                "grow_mask_by": 0,
                "selection_hash": mask_binding.selection_hash,
                "policy_version": mask_binding.mask_policy_version,
            },
            "artifact_sha256": {
                name: ref.sha256
                for name, ref in {
                    "image": mask_binding.image_ref,
                    "mask": mask_binding.mask_ref,
                    "canonical": mask_binding.canonical_ref,
                    "canonical_edit_mask": mask_binding.canonical_edit_mask_ref,
                    "selection": mask_binding.selection_ref,
                    "view_transform": mask_binding.view_transform_ref,
                }.items()
            },
        }
        contract_digest = hashlib.sha256(canonical_json(evidence)).hexdigest()
        contract_path = destination.with_name(f"{graph_digest}.contract.json")
        contract_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return CompiledWorkflow(
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            graph=graph,
            graph_sha256=graph_digest,
            models=recipe.models,
            outputs=recipe.outputs,
            local_graph_path=str(destination.resolve()),
            local_contract_path=str(contract_path.resolve()),
            contract_sha256=contract_digest,
            validation={
                "recipe": True,
                "native_nodes": True,
                "object_info": object_info is not None,
                "masked": True,
                "trusted_artifacts": True,
                "mask_policy": True,
                "no_edit_pixels": empty_mask,
            },
        )

    def validate_masked_artifacts(
        self, plan: MaskedGenerationPlan, trusted_artifacts: Any, trusted_sources: Any | None = None
    ) -> dict[str, Any]:
        """Validate all frozen masked inputs before a backend uploads or submits anything."""

        if not callable(getattr(trusted_artifacts, "read", None)):
            raise ValidationError("Masked artifact validation requires a trusted artifact store")
        if trusted_sources is None:
            raise ValidationError("Masked artifact validation requires trusted source bindings")
        self._validate_masked_plan_metadata(plan)
        return self._read_masked_artifacts(plan, trusted_artifacts, trusted_sources)

    @staticmethod
    def _validate_masked_plan_metadata(plan: MaskedGenerationPlan) -> WorkflowRecipe:
        if plan.backend != "comfy" or plan.intent != GenerationIntent.image_to_image:
            raise ValidationError("Masked inpaint compilation requires Comfy image-to-image")
        if plan.recipe != "sdxl-inpaint":
            raise ValidationError("Masked plans must use the native sdxl-inpaint recipe")
        recipe = load_recipe(plan.recipe)
        if recipe.intent != plan.intent or recipe.id != plan.envelope.recipe:
            raise ValidationError("Recipe intent does not match the approved masked plan")
        if plan.model not in recipe.models:
            raise ValidationError("Recipe model does not match the approved masked plan")
        if not set(plan.envelope.mutable_parameters).issubset(set(recipe.mutable_parameters)):
            raise ValidationError("Execution envelope exposes tunables not declared by the recipe")
        if not set(recipe.allowed_nodes).issubset(NATIVE_NODES):
            raise ValidationError("Recipe contains a node outside the native allowlist")
        contract = load_workflow_contract("sdxl-inpaint")
        if contract.models != recipe.models or set(contract.nodes) != set(recipe.allowed_nodes):
            raise ValidationError("Inpaint recipe and workflow contract are out of sync")
        WorkflowCompiler._validate_envelope(plan)
        unknown_parameters = sorted(set(plan.parameters) - set(recipe.parameter_bounds))
        if unknown_parameters:
            raise ValidationError(
                "Parameters are not declared by the inpaint recipe", details={"unknown": unknown_parameters}
            )
        for name, value in plan.parameters.items():
            WorkflowCompiler._validate_parameter(name, value, recipe.parameter_bounds[name])
        frozen_parameters = {"steps", "cfg", "denoise"}
        if not frozen_parameters.issubset(plan.parameters):
            raise ValidationError("Masked plan must freeze steps, cfg and denoise explicitly")
        if frozen_parameters & set(plan.envelope.mutable_parameters):
            raise ValidationError("Masked plan exposes frozen sampler parameters as mutable")
        return recipe

    @staticmethod
    def _validate_upload_handle(value: str, role: str) -> None:
        if not isinstance(value, str):
            raise ValidationError(f"Uploaded {role} handle must be a relative server name")
        normalized = value.replace("\\", "/")
        parts = normalized.split("/")
        if (
            not value.strip()
            or normalized.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
            or ":" in parts[0]
        ):
            raise ValidationError(f"Uploaded {role} handle must be a relative server name")

    @staticmethod
    def _open_image(data: bytes, role: str) -> Image.Image:
        try:
            with Image.open(io.BytesIO(data)) as loaded:
                if loaded.format != "PNG":
                    raise ValidationError(f"Masked {role} must be a PNG")
                loaded.load()
                return loaded.copy()
        except ValidationError:
            raise
        except (OSError, ValueError) as exc:
            raise ValidationError(f"Masked artifact is not a readable {role} image") from exc

    @classmethod
    def _read_masked_artifacts(
        cls, plan: MaskedGenerationPlan, trusted_artifacts: Any, trusted_sources: Any | None
    ) -> dict[str, Any]:
        binding = plan.image_mask
        refs = {
            "image": binding.image_ref,
            "mask": binding.mask_ref,
            "canonical": binding.canonical_ref,
            "canonical_mask": binding.canonical_edit_mask_ref,
            "selection": binding.selection_ref,
            "transform": binding.view_transform_ref,
        }
        raw = {name: trusted_artifacts.read(ref) for name, ref in refs.items()}
        image = cls._open_image(raw["image"], "image")
        mask = cls._open_image(raw["mask"], "mask")
        canonical = cls._open_image(raw["canonical"], "canonical image")
        canonical_mask = cls._open_image(raw["canonical_mask"], "canonical mask")
        if image.size != (binding.width, binding.height) or canonical.size != (
            binding.canonical_width,
            binding.canonical_height,
        ):
            raise ValidationError("Masked image dimensions do not match the frozen binding")
        if mask.mode != "L" or canonical_mask.mode != "L":
            raise ValidationError("Inpaint masks must be grayscale PNGs")
        if mask.size != image.size or canonical_mask.size != canonical.size:
            raise ValidationError("Image and mask dimensions must remain paired")
        if mask.size[0] > 1024 or mask.size[1] > 1024:
            raise ValidationError("Prepared inpaint dimensions exceed the 1024 work-view limit")
        if mask.size[0] % 8 or mask.size[1] % 8:
            raise ValidationError("Prepared inpaint dimensions must be padded to multiples of eight")

        if trusted_sources is None:
            raise ValidationError("Masked artifact validation requires trusted source bindings")
        try:
            selection_payload = json.loads(raw["selection"].decode("utf-8"))
            selection = validate_selection(trusted_artifacts, plan.task_id, selection_payload, trusted_sources)
            transform = json.loads(raw["transform"].decode("utf-8"))
        except (PipelineError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValidationError("Masked selection or view transform is invalid") from exc
        if not isinstance(trusted_sources, Mapping) or len(selection.sources) != 1:
            raise ValidationError("Masked plan must bind exactly one trusted source")
        selected_source = selection.sources[0]
        trusted_source = trusted_sources.get(selected_source.source_id)
        if not isinstance(trusted_source, TrustedSource):
            raise ValidationError("Masked selection source is not trusted")
        if trusted_source.canonical_ref != binding.canonical_ref:
            raise ValidationError("Masked canonical image is not the trusted source canonical")
        if trusted_source.layout_ref is not None and selected_source.layout_ref != trusted_source.layout_ref:
            raise ValidationError("Masked selection layout is not the trusted source layout")
        if not isinstance(transform, dict):
            raise ValidationError("Masked view transform must be an object")
        if transform.get("canonical_size") != [binding.canonical_width, binding.canonical_height]:
            raise ValidationError("View transform canonical size disagrees with binding")
        if transform.get("schema_version") != 1:
            raise ValidationError("View transform schema version is unsupported")
        if transform.get("model_size") != [binding.width, binding.height]:
            raise ValidationError("View transform prepared size disagrees with binding")
        if transform.get("crop") != list(binding.crop):
            raise ValidationError("View transform crop disagrees with binding")
        if transform.get("resize") != list(binding.resize) or transform.get("pad") != list(binding.pad):
            raise ValidationError("View transform resize/pad disagrees with binding")
        inverse_scale = transform.get("inverse_scale")
        if (
            not isinstance(inverse_scale, list)
            or len(inverse_scale) != 2
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                   for value in inverse_scale)
        ):
            raise ValidationError("View transform inverse scale is invalid")
        crop_x1, crop_y1, crop_x2, crop_y2 = binding.crop
        resize_w, resize_h = binding.resize
        expected_scale = ((crop_x2 - crop_x1) / resize_w, (crop_y2 - crop_y1) / resize_h)
        if any(
            abs(float(actual) - expected) > 1e-9
            for actual, expected in zip(inverse_scale, expected_scale, strict=True)
        ):
            raise ValidationError("View transform inverse scale is inconsistent with crop and resize")
        inverse = transform.get("inverse")
        if (
            not isinstance(inverse, list)
            or len(inverse) != 3
            or any(
                not isinstance(row, list)
                or len(row) != 3
                or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                       for value in row)
                for row in inverse
            )
        ):
            raise ValidationError("View transform inverse matrix is invalid")
        left, top, _, _ = binding.pad
        expected_inverse = [
            [expected_scale[0], 0.0, crop_x1 - left * expected_scale[0]],
            [0.0, expected_scale[1], crop_y1 - top * expected_scale[1]],
            [0.0, 0.0, 1.0],
        ]
        if any(
            abs(float(inverse[row][column]) - expected_inverse[row][column]) > 1e-9
            for row in range(3)
            for column in range(3)
        ):
            raise ValidationError("View transform inverse matrix is inconsistent with crop, resize and pad")

        target_boxes: list[tuple[int, int, int, int]] = []
        keep_boxes: list[tuple[int, int, int, int]] = []
        remove_boxes: list[tuple[int, int, int, int]] = []
        # ``validate_selection`` performs the complete source/layout trust
        # checks.  Reuse the same loader for the geometry so automatic model
        # bboxes (which may be finite floats) receive the canonical
        # floor/ceil enclosing-pixel normalization used by selection.py.
        # Parsing the raw JSON here would create a second, weaker geometry
        # contract and could disagree with the selection validator.
        elements: dict[str, tuple[int, int, int, int]] = {}
        if selected_source.layout_ref is not None:
            try:
                layout_payload = _load_layout(
                    trusted_artifacts,
                    selected_source.layout_ref,
                    task_id=plan.task_id,
                    source=trusted_source,
                )
            except PipelineError as exc:
                raise ValidationError("Selection layout is invalid") from exc
            elements = _layout_elements(layout_payload)
        for source in selection.sources:
            for region in source.target_regions:
                if region.kind == "bbox":
                    target_boxes.append(tuple(region.xyxy))
                elif region.element_id in elements:
                    target_boxes.append(elements[region.element_id])
                else:
                    raise ValidationError("Selection target element is absent from the frozen layout")
            for element_id in [*source.keep_elements, *source.remove_elements]:
                if element_id not in elements:
                    raise ValidationError("Selection keep/remove element is absent from the frozen layout")
            keep_boxes.extend(elements[element_id] for element_id in source.keep_elements)
            remove_boxes.extend(elements[element_id] for element_id in source.remove_elements)
        if not target_boxes:
            raise ValidationError("Masked selection has no target region")
        for remove_box in remove_boxes:
            if not _covered_by_union(remove_box, target_boxes):
                raise ValidationError("Selection remove element escapes the approved target region")
        for y in range(canonical_mask.height):
            for x in range(canonical_mask.width):
                if canonical_mask.getpixel((x, y)) and not any(
                    x1 <= x < x2 and y1 <= y < y2 for x1, y1, x2, y2 in target_boxes
                ):
                    raise ValidationError("Final mask escapes the approved target region")
                if canonical_mask.getpixel((x, y)) and any(
                    x1 <= x < x2 and y1 <= y < y2 for x1, y1, x2, y2 in keep_boxes
                ):
                    raise ValidationError("Final mask edits a frozen keep element")

        canonical_crop = canonical.crop((crop_x1, crop_y1, crop_x2, crop_y2))
        canonical_mask_crop = canonical_mask.crop((crop_x1, crop_y1, crop_x2, crop_y2))
        expected_image = resize_and_pad_image(
            canonical_crop, resize=binding.resize, pad=binding.pad, resample=Image.Resampling.LANCZOS
        )
        expected_mask = prepare_edit_mask(
            canonical_mask_crop,
            channel="red",
            polarity="white_is_edit",
            resize=binding.resize,
            pad=binding.pad,
        )
        if (
            expected_image.mode != image.mode
            or expected_image.size != image.size
            or expected_image.tobytes() != image.tobytes()
        ):
            raise ValidationError("Prepared image does not match the frozen canonical transform")
        if expected_mask.tobytes() != mask.tobytes():
            raise ValidationError("Prepared mask does not match the frozen canonical transform")
        return {
            "image": image,
            "mask": mask,
            "canonical": canonical,
            "canonical_mask": canonical_mask,
            "selection": selection,
            "transform": transform,
        }

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
    def _compile_sdxl_inpaint(graph: dict[str, Any], image: str, mask: str) -> dict[str, Any]:
        result = copy.deepcopy(graph)
        result["10"]["inputs"]["image"] = image
        result["12"]["inputs"]["image"] = mask
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
