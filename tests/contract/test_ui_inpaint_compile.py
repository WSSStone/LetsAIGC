"""T025 contract tests for the native SDXL inpaint compilation boundary.

These tests intentionally stop at compile/CPU-artifact boundaries.  They never
submit a ComfyUI prompt or require a model.  The only expected failures before
T026 are the explicit compiler-entry checks below.
"""

from __future__ import annotations

import copy
import inspect
import io
import json
from pathlib import Path

import pytest
import yaml
from PIL import Image

from letsaigc.agent.storage import AgentStore
from letsaigc.assets.store import ArtifactStore
from letsaigc.errors import ValidationError
from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas import ExecutionEnvelope, GenerationIntent, GenerationPlan, TaskBudget
from letsaigc.schemas.agent import MaskedGenerationPlan
from letsaigc.schemas.pipeline import ArtifactRef
from letsaigc.ui_analysis.selection import TrustedSource
from letsaigc.workflows.compiler import WorkflowCompiler, load_recipe

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests/fixtures/contracts/ui-inpaint-compile-golden.json"
EDIT_FIXTURE = ROOT / "tests/fixtures/contracts/ui-edit-contracts.json"
INPAINT_ARTIFACTS = (
    ROOT / "configs/workflows/recipes/sdxl-inpaint.yaml",
    ROOT / "workflows/ui/sdxl-inpaint.json",
    ROOT / "workflows/api/sdxl-inpaint.json",
    ROOT / "workflows/contracts/sdxl-inpaint.yaml",
)

NATIVE_INPAINT_NODES = {
    "CheckpointLoaderSimple",
    "LoadImage",
    "ImageToMask",
    "VAEEncodeForInpaint",
    "CLIPTextEncode",
    "KSampler",
    "VAEDecode",
    "SaveImage",
}


def _golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _plan_from_golden(spec: dict) -> GenerationPlan:
    inputs = _golden()["compiler_inputs"]
    budget = TaskBudget.model_validate(inputs["budget"])
    envelope = ExecutionEnvelope(
        backend=inputs["backend"],
        model=inputs["model"],
        recipe=spec["recipe"],
        max_width=inputs["max_width"],
        max_height=inputs["max_height"],
        max_steps=inputs["max_steps"],
        allowed_tools=inputs["allowed_tools"],
        mutable_parameters=[
            name for name in inputs["mutable_parameters"] if name != "denoise" or spec["recipe"] == "sdxl-i2i"
        ],
        budget=budget,
    )
    return GenerationPlan(
        task_id=inputs["task_id"],
        session_id=inputs["session_id"],
        intent=GenerationIntent(spec["intent"]),
        user_intent=inputs["user_intent"],
        backend=inputs["backend"],
        model=inputs["model"],
        recipe=spec["recipe"],
        parameters=spec["parameters"],
        acceptance_criteria=inputs["acceptance_criteria"],
        envelope=envelope,
    )


def _png(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _masked_plan(tmp_path: Path) -> tuple[MaskedGenerationPlan, ArtifactStore, str, str]:
    """Build a task-scoped plan whose refs and bytes agree exactly."""

    data = json.loads(EDIT_FIXTURE.read_text(encoding="utf-8"))
    base = copy.deepcopy(data["masked_plan"])
    task_id = base["task_id"]
    store = ArtifactStore(tmp_path / "artifacts")
    source_ids = ["source-1"]

    canonical_image = Image.new("RGB", (8, 6))
    canonical_image.putdata([(x * 17, y * 29, (x * 13 + y * 7) % 256) for y in range(6) for x in range(8)])
    prepared_image = Image.new("RGB", (8, 8))
    prepared_image.paste(canonical_image, (0, 0))
    canonical_mask = Image.new("L", (8, 6), 0)
    canonical_mask.putpixel((3, 2), 255)
    canonical_mask.putpixel((4, 2), 255)
    canonical_mask.putpixel((3, 3), 255)
    canonical_mask.putpixel((4, 3), 255)
    prepared_mask = Image.new("L", (8, 8), 0)
    prepared_mask.paste(canonical_mask, (0, 0))
    image_bytes = _png(prepared_image)
    canonical_bytes = _png(canonical_image)
    mask_bytes = _png(prepared_mask)
    canonical_mask_bytes = _png(canonical_mask)
    image_ref = store.put(task_id, "fixture", image_bytes, role="image", media_type="image/png", source_ids=source_ids)
    canonical_ref = store.put(
        task_id, "fixture", canonical_bytes, role="canonical", media_type="image/png", source_ids=source_ids
    )
    mask_ref = store.put(
        task_id, "fixture", mask_bytes, role="edit_mask", media_type="image/png", source_ids=source_ids
    )
    canonical_mask_ref = store.put(
        task_id, "fixture", canonical_mask_bytes, role="edit_mask", media_type="image/png", source_ids=source_ids
    )
    selection = {
        "schema_version": 1,
        "sources": [
            {
                "source_id": "source-1",
                "original_sha256": canonical_ref.sha256,
                "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 8, 6]}],
                "keep_elements": [],
                "remove_elements": [],
            }
        ],
    }
    selection_ref = store.put(
        task_id,
        "fixture",
        json.dumps(selection, sort_keys=True, separators=(",", ":")).encode(),
        role="selection",
        source_ids=source_ids,
    )
    transform = {
        "schema_version": 1,
        "canonical_size": [8, 6],
        "model_size": [8, 8],
        "crop": [0, 0, 8, 6],
        "resize": [8, 6],
        "pad": [0, 0, 0, 2],
        "inverse_scale": [1.0, 1.0],
        "inverse": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
    }
    view_ref = store.put(
        task_id,
        "fixture",
        json.dumps(transform, sort_keys=True, separators=(",", ":")).encode(),
        role="view_transform",
        source_ids=source_ids,
    )
    binding = base["image_mask"]
    for key, ref in {
        "image_ref": image_ref,
        "mask_ref": mask_ref,
        "canonical_ref": canonical_ref,
        "canonical_edit_mask_ref": canonical_mask_ref,
        "selection_ref": selection_ref,
        "view_transform_ref": view_ref,
    }.items():
        binding[key] = ref.model_dump(mode="json")
    binding.update(
        {
            "selection_hash": selection_ref.sha256,
            "width": 8,
            "height": 8,
            "canonical_width": 8,
            "canonical_height": 6,
            "crop": [0, 0, 8, 6],
            "resize": [8, 6],
            "pad": [0, 0, 0, 2],
        }
    )
    base["parameters"] = {
        "prompt": "restore the selected game background",
        "negative_prompt": "text, watermark",
        "seed": 7,
        "steps": 20,
        "cfg": 6,
        "denoise": 0.6,
    }
    base["envelope"]["max_width"] = 64
    base["envelope"]["max_height"] = 48
    base["envelope"]["max_steps"] = 60
    base["envelope"]["mutable_parameters"] = ["prompt", "negative_prompt", "seed"]
    return MaskedGenerationPlan.model_validate(base), store, "input.png", "mask.png"


def _assert_t026_entry(compiler: WorkflowCompiler) -> None:
    """Only the absent new keyword boundary is an expected pre-T026 xfail."""

    parameters = inspect.signature(compiler.compile).parameters
    if "uploaded_mask" not in parameters or "trusted_artifacts" not in parameters:
        pytest.xfail("T026: compiler masked-input entry is not implemented")


def _trusted_sources(plan: MaskedGenerationPlan, store: ArtifactStore) -> dict[str, TrustedSource]:
    selection = json.loads(store.read(plan.image_mask.selection_ref).decode("utf-8"))
    source = selection["sources"][0]
    layout_ref = ArtifactRef.model_validate(source["layout_ref"]) if source.get("layout_ref") else None
    original_ref = store.put(
        plan.task_id,
        "trusted-original",
        store.read(plan.image_mask.canonical_ref),
        role="original",
        media_type="image/png",
        source_ids=["source-1"],
    )
    return {
        source["source_id"]: TrustedSource(
            source_id=source["source_id"],
            original_ref=original_ref,
            canonical_ref=plan.image_mask.canonical_ref,
            layout_ref=layout_ref,
        )
    }


def _cropped_element_plan(
    tmp_path: Path, *, layout_role: str = "review_layout"
) -> tuple[MaskedGenerationPlan, ArtifactStore, str, str]:
    plan, store, image_path, mask_path = _masked_plan(tmp_path)
    task_id = plan.task_id
    source_ids = ["source-1"]
    canonical = Image.new("RGB", (16, 12))
    canonical.putdata([(x * 11, y * 17, (x * 7 + y * 13) % 256) for y in range(12) for x in range(16)])
    canonical_mask = Image.new("L", canonical.size, 0)
    canonical_mask.putpixel((4, 4), 255)
    canonical_mask.putpixel((5, 4), 255)
    crop = (2, 2, 10, 10)
    prepared = canonical.crop(crop)
    prepared_mask = canonical_mask.crop(crop)
    canonical_ref = store.put(
        task_id, "cropped", _png(canonical), role="canonical", media_type="image/png", source_ids=source_ids
    )
    canonical_mask_ref = store.put(
        task_id, "cropped", _png(canonical_mask), role="edit_mask", media_type="image/png", source_ids=source_ids
    )
    image_ref = store.put(
        task_id, "cropped", _png(prepared), role="image", media_type="image/png", source_ids=source_ids
    )
    mask_ref = store.put(
        task_id, "cropped", _png(prepared_mask), role="edit_mask", media_type="image/png", source_ids=source_ids
    )
    layout = {
        "schema_version": 2 if layout_role == "review_layout" else 1,
        "task_id": task_id,
        "source_id": "source-1",
        "width": 16,
        "height": 12,
        "elements": [
            {
                "element_id": "target",
                "base_type": "container",
                "bbox": [2.2, 2.2, 7.2, 7.2] if layout_role == "layout" else [2, 2, 8, 8],
            },
            {
                "element_id": "keep",
                "base_type": "image",
                "bbox": [8.2, 2.2, 9.1, 7.5] if layout_role == "layout" else [8, 2, 10, 8],
            },
        ],
        "texts": [],
        "user_declared": "",
    }
    if layout_role == "layout":
        layout.update(
            {
                "canonical_ref": canonical_ref.model_dump(mode="json"),
                "canonical_sha256": canonical_ref.sha256,
            }
        )
    layout_ref = store.put(
        task_id,
        "cropped",
        json.dumps(layout, sort_keys=True, separators=(",", ":")).encode(),
        role=layout_role,
        media_type="application/json",
        source_ids=source_ids,
    )
    selection = {
        "schema_version": 1,
        "sources": [{
            "source_id": "source-1",
            "original_sha256": canonical_ref.sha256,
            "layout_ref": layout_ref.model_dump(mode="json"),
            "target_regions": [{"kind": "element", "element_id": "target"}],
            "keep_elements": ["keep"],
            "remove_elements": [],
        }],
    }
    selection_ref = store.put(
        task_id,
        "cropped",
        json.dumps(selection, sort_keys=True, separators=(",", ":")).encode(),
        role="selection",
        media_type="application/json",
        source_ids=source_ids,
    )
    transform = {
        "schema_version": 1,
        "canonical_size": [16, 12],
        "model_size": [8, 8],
        "crop": list(crop),
        "resize": [8, 8],
        "pad": [0, 0, 0, 0],
        "inverse_scale": [1.0, 1.0],
        "inverse": [[1.0, 0.0, 2.0], [0.0, 1.0, 2.0], [0.0, 0.0, 1.0]],
    }
    view_ref = store.put(
        task_id,
        "cropped",
        json.dumps(transform, sort_keys=True, separators=(",", ":")).encode(),
        role="view_transform",
        source_ids=source_ids,
    )
    binding = plan.image_mask.model_copy(update={
        "image_ref": image_ref,
        "mask_ref": mask_ref,
        "canonical_ref": canonical_ref,
        "canonical_edit_mask_ref": canonical_mask_ref,
        "selection_ref": selection_ref,
        "view_transform_ref": view_ref,
        "selection_hash": selection_ref.sha256,
        "width": 8,
        "height": 8,
        "canonical_width": 16,
        "canonical_height": 12,
        "crop": crop,
        "resize": [8, 8],
        "pad": [0, 0, 0, 0],
    })
    return plan.model_copy(update={"image_mask": binding}), store, image_path, mask_path


def _compile_masked(
    compiler: WorkflowCompiler,
    plan: MaskedGenerationPlan,
    store: ArtifactStore,
    image_path: str,
    mask_path: str,
):
    _assert_t026_entry(compiler)
    return compiler.compile(
        plan,
        uploaded_images=[image_path],
        uploaded_mask=mask_path,
        trusted_artifacts=store,
        trusted_sources=_trusted_sources(plan, store),
    )


def _node_types(document: object) -> set[str]:
    found: set[str] = set()
    if isinstance(document, dict):
        for key in ("class_type", "type", "node_type"):
            value = document.get(key)
            if isinstance(value, str):
                found.add(value)
        for value in document.values():
            found.update(_node_types(value))
    elif isinstance(document, list):
        for value in document:
            found.update(_node_types(value))
    return found


@pytest.mark.parametrize("spec", _golden()["plans"], ids=lambda spec: spec["recipe"])
def test_legacy_t2i_and_i2i_compilation_golden_is_unchanged(spec, tmp_path: Path) -> None:
    compiler = WorkflowCompiler(AgentStore(tmp_path / "agent"))
    plan = _plan_from_golden(spec)
    uploaded = ["safe/input.png"] if spec["intent"] == "image_to_image" else None
    compiled = compiler.compile(plan, uploaded_images=uploaded)
    assert compiled.graph_sha256 == spec["graph_sha256"]
    assert compiled.contract_sha256 == spec["contract_sha256"]


def test_native_inpaint_artifacts_are_four_synchronized_native_contracts() -> None:
    if not INPAINT_ARTIFACTS[0].is_file():
        pytest.xfail("T026: native SDXL inpaint four-artifact bundle is not implemented")
    missing = [path for path in INPAINT_ARTIFACTS if not path.is_file()]
    assert not missing, f"T026 native inpaint bundle is incomplete: {missing}"
    recipe_raw = yaml.safe_load(INPAINT_ARTIFACTS[0].read_text(encoding="utf-8"))
    workflow_ui = json.loads(INPAINT_ARTIFACTS[1].read_text(encoding="utf-8"))
    workflow_api = json.loads(INPAINT_ARTIFACTS[2].read_text(encoding="utf-8"))
    contract = yaml.safe_load(INPAINT_ARTIFACTS[3].read_text(encoding="utf-8"))
    recipe = load_recipe("sdxl-inpaint")

    assert recipe_raw["id"] == recipe.id == "sdxl-inpaint"
    assert recipe_raw["intent"] == recipe.intent.value == "image_to_image"
    assert recipe_raw["models"] == ["sdxl-base-1.0"]
    assert set(NATIVE_INPAINT_NODES).issubset(set(recipe.allowed_nodes))
    assert set(_node_types(workflow_ui)) >= NATIVE_INPAINT_NODES
    assert set(_node_types(workflow_api)) >= NATIVE_INPAINT_NODES
    assert contract["ui_workflow"] == "workflows/ui/sdxl-inpaint.json"
    assert contract["api_workflow"] == "workflows/api/sdxl-inpaint.json"
    assert set(contract["nodes"]) == set(recipe.allowed_nodes)
    assert contract["input_schema"]["additionalProperties"] is False
    mask_contract = contract["defaults"]["mask"]
    assert mask_contract["channel"] == "red"
    assert mask_contract["polarity"] == "white_is_edit"
    assert mask_contract["grow_mask_by"] == 0


def test_masked_compile_dispatches_to_inpaint_nodes_and_requires_a_verified_pair(tmp_path: Path) -> None:
    plan, store, image_path, mask_path = _masked_plan(tmp_path)
    compiler = WorkflowCompiler(AgentStore(tmp_path / "compiled"))
    compiled = _compile_masked(compiler, plan, store, image_path, mask_path)
    classes = _node_types(compiled.graph)
    assert set(NATIVE_INPAINT_NODES).issubset(classes)
    assert "VAEEncode" not in classes
    assert "EmptyLatentImage" not in classes
    mask_nodes = [node for node in compiled.graph.values() if node.get("class_type") == "ImageToMask"]
    assert len(mask_nodes) == 1
    assert mask_nodes[0]["inputs"]["channel"] == "red"
    inpaint_nodes = [node for node in compiled.graph.values() if node.get("class_type") == "VAEEncodeForInpaint"]
    assert len(inpaint_nodes) == 1
    assert inpaint_nodes[0]["inputs"]["grow_mask_by"] == 0
    assert compiled.graph["3"]["inputs"]["steps"] == 20
    assert compiled.graph["3"]["inputs"]["cfg"] == 6
    assert compiled.graph["3"]["inputs"]["denoise"] == 0.6
    evidence = json.loads(Path(compiled.local_contract_path).read_text(encoding="utf-8"))
    assert evidence["frozen_parameters"] == {"steps": 20, "cfg": 6, "denoise": 0.6}

    with pytest.raises((ValidationError, ValueError)):
        compiler.compile(plan, uploaded_images=[image_path], uploaded_mask=None, trusted_artifacts=store)


def test_masked_compile_rejects_changed_or_mismatched_artifact_bytes(tmp_path: Path) -> None:
    plan, store, image_path, mask_path = _masked_plan(tmp_path)
    compiler = WorkflowCompiler(AgentStore(tmp_path / "compiled"))
    _assert_t026_entry(compiler)
    wrong_size = Image.new("L", (9, 8), 255)
    wrong_size_ref = store.put(plan.task_id, "wrong-size", _png(wrong_size), role="edit_mask", media_type="image/png")
    changed_plan = plan.model_copy(
        update={"image_mask": plan.image_mask.model_copy(update={"mask_ref": wrong_size_ref})}
    )
    with pytest.raises((ValidationError, ValueError)):
        _compile_masked(compiler, changed_plan, store, image_path, mask_path)

    # Swapping a prepared mask into the canonical slot violates the frozen
    # resize/pad transform even though both refs have the edit_mask role.
    foreign_plan = plan.model_copy(
        update={"image_mask": plan.image_mask.model_copy(update={"canonical_edit_mask_ref": plan.image_mask.mask_ref})}
    )
    with pytest.raises((ValidationError, ValueError)):
        _compile_masked(compiler, foreign_plan, store, image_path, mask_path)

    # ArtifactStore must detect a byte change before the compiler can build a
    # graph from it; the compiler must not trust a matching path alone.
    store.resolve(plan.image_mask.mask_ref).write_bytes(b"tampered")
    with pytest.raises((PipelineError, ValidationError, ValueError)):
        _compile_masked(compiler, plan, store, image_path, mask_path)


def test_masked_compile_expands_element_target_and_protects_keep_area_in_non_full_crop(tmp_path: Path) -> None:
    plan, store, image_path, mask_path = _cropped_element_plan(tmp_path)
    compiler = WorkflowCompiler(AgentStore(tmp_path / "compiled"))
    compiled = _compile_masked(compiler, plan, store, image_path, mask_path)
    assert compiled.validation["masked"] is True
    assert compiled.validation["no_edit_pixels"] is False

    canonical_mask = Image.open(io.BytesIO(store.read(plan.image_mask.canonical_edit_mask_ref))).convert("L")
    assert canonical_mask.getpixel((4, 4)) == 255
    assert canonical_mask.getpixel((8, 4)) == 0

    # A prepared mask pixel over the frozen keep element is rejected even when
    # the target element itself remains valid.
    bad_mask = canonical_mask.copy()
    bad_mask.putpixel((8, 4), 255)
    bad_ref = store.put(
        plan.task_id,
        "bad-keep",
        _png(bad_mask.crop(plan.image_mask.crop)),
        role="edit_mask",
        media_type="image/png",
    )
    bad_plan = plan.model_copy(update={"image_mask": plan.image_mask.model_copy(update={"mask_ref": bad_ref})})
    with pytest.raises((ValidationError, ValueError)):
        _compile_masked(compiler, bad_plan, store, image_path, mask_path)


def test_masked_compile_reuses_automatic_float_layout_geometry(tmp_path: Path) -> None:
    plan, store, image_path, mask_path = _cropped_element_plan(tmp_path, layout_role="layout")
    compiled = _compile_masked(WorkflowCompiler(AgentStore(tmp_path / "compiled")), plan, store, image_path, mask_path)
    assert compiled.validation["masked"] is True
    assert compiled.validation["no_edit_pixels"] is False


def test_masked_compile_rejects_tampered_inverse_transform(tmp_path: Path) -> None:
    plan, store, image_path, mask_path = _cropped_element_plan(tmp_path)
    transform = json.loads(store.read(plan.image_mask.view_transform_ref).decode("utf-8"))
    transform["inverse"][0][2] = 3.0
    bad_ref = store.put(
        plan.task_id,
        "bad-transform",
        json.dumps(transform, sort_keys=True, separators=(",", ":")).encode(),
        role="view_transform",
    )
    bad_plan = plan.model_copy(
        update={"image_mask": plan.image_mask.model_copy(update={"view_transform_ref": bad_ref})}
    )
    with pytest.raises((ValidationError, ValueError)):
        _compile_masked(WorkflowCompiler(AgentStore(tmp_path / "compiled")), bad_plan, store, image_path, mask_path)


def test_masked_compile_rejects_another_image_source_in_the_same_task(tmp_path: Path) -> None:
    plan, store, image_path, mask_path = _masked_plan(tmp_path)
    selection = json.loads(store.read(plan.image_mask.selection_ref).decode("utf-8"))
    selection["sources"].append({
        "source_id": "source-2",
        "original_sha256": plan.image_mask.image_ref.sha256,
        "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 8, 6]}],
        "keep_elements": [],
        "remove_elements": [],
    })
    selection_ref = store.put(
        plan.task_id,
        "other-source",
        json.dumps(selection, sort_keys=True, separators=(",", ":")).encode(),
        role="selection",
        media_type="application/json",
    )
    bad_plan = plan.model_copy(update={
        "image_mask": plan.image_mask.model_copy(update={
            "selection_ref": selection_ref,
            "selection_hash": selection_ref.sha256,
        })
    })
    with pytest.raises((ValidationError, ValueError)):
        _compile_masked(WorkflowCompiler(AgentStore(tmp_path / "compiled")), bad_plan, store, image_path, mask_path)


def test_empty_mask_compiles_to_no_edit_without_a_generation_graph(tmp_path: Path) -> None:
    plan, store, image_path, mask_path = _masked_plan(tmp_path)
    empty_canonical = Image.new("L", (8, 6), 0)
    empty_prepared = Image.new("L", (8, 8), 0)
    canonical_ref = store.put(
        plan.task_id,
        "empty",
        _png(empty_canonical),
        role="edit_mask",
        media_type="image/png",
        source_ids=["source-1"],
    )
    prepared_ref = store.put(
        plan.task_id,
        "empty",
        _png(empty_prepared),
        role="edit_mask",
        media_type="image/png",
        source_ids=["source-1"],
    )
    empty_plan = plan.model_copy(update={
        "image_mask": plan.image_mask.model_copy(update={
            "mask_ref": prepared_ref,
            "canonical_edit_mask_ref": canonical_ref,
        })
    })
    compiled = _compile_masked(
        WorkflowCompiler(AgentStore(tmp_path / "compiled")), empty_plan, store, image_path, mask_path
    )
    assert compiled.graph == {}
    assert compiled.validation["no_edit_pixels"] is True


def test_masked_plan_can_freeze_a_different_in_bounds_sampler_value(tmp_path: Path) -> None:
    plan, store, image_path, mask_path = _masked_plan(tmp_path)
    parameters = {**plan.parameters, "steps": 26}
    legal_plan = plan.model_copy(update={
        "parameters": parameters,
    })
    compiled = _compile_masked(
        WorkflowCompiler(AgentStore(tmp_path / "compiled")), legal_plan, store, image_path, mask_path
    )
    assert compiled.graph["3"]["inputs"]["steps"] == 26


def test_masked_compile_rejects_non_png_image_artifacts(tmp_path: Path) -> None:
    plan, store, image_path, mask_path = _masked_plan(tmp_path)
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), (20, 30, 40)).save(stream, format="JPEG")
    jpeg_ref = store.put(plan.task_id, "jpeg", stream.getvalue(), role="image", media_type="image/jpeg")
    bad_plan = plan.model_copy(update={"image_mask": plan.image_mask.model_copy(update={"image_ref": jpeg_ref})})
    with pytest.raises((ValidationError, ValueError)):
        _compile_masked(WorkflowCompiler(AgentStore(tmp_path / "compiled")), bad_plan, store, image_path, mask_path)
