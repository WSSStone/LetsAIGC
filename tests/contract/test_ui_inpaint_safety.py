"""Safety contracts for the T026 masked ComfyUI boundary.

These tests stop at local artifact preparation, compilation, and the provider
submission boundary.  They deliberately use synthetic PNGs and a fake client;
they never start ComfyUI, load a model, or use a GPU.
"""

from __future__ import annotations

import copy
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from letsaigc.agent.storage import AgentStore
from letsaigc.assets.store import ArtifactStore
from letsaigc.errors import ValidationError
from letsaigc.schemas.agent import CompiledWorkflow, MaskedGenerationPlan
from letsaigc.schemas.ui_review import ReviewDocument
from letsaigc.ui_analysis.inpaint import restore_to_canonical
from letsaigc.ui_analysis.selection import TrustedSource
from letsaigc.workflows.compiler import WorkflowCompiler


def _png(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _put_json(store: ArtifactStore, task_id: str, operation_id: str, payload: dict, role: str, source_ids=None):
    return store.put(
        task_id,
        operation_id,
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        role=role,
        source_ids=source_ids,
    )


def _fixture(
    tmp_path: Path,
    *,
    source_ids: list[str] | None = None,
    canonical: Image.Image | None = None,
    canonical_mask: Image.Image | None = None,
):
    """Build one complete non-full-view binding with resize and padding."""

    source_ids = source_ids or ["source-1"]
    task_id = "edit-child"
    session_id = "safety-session"
    store = ArtifactStore(tmp_path / "artifacts")
    if canonical is None:
        canonical = Image.new("RGB", (16, 12))
        canonical.putdata(
            [(x * 11, y * 17, (x * 7 + y * 13) % 256) for y in range(canonical.height) for x in range(canonical.width)]
        )
    if canonical_mask is None:
        canonical_mask = Image.new("L", canonical.size, 0)
        for y in range(2, 8):
            for x in range(2, 8):
                canonical_mask.putpixel((x, y), 255)

    original_ref = store.put(
        task_id, "safety", _png(canonical), role="original", media_type="image/png", source_ids=["lineage-1"]
    )
    source_ids = [original_ref.artifact_id]
    crop = (2, 2, 10, 10)
    resize = (6, 6)
    pad = (1, 1, 1, 1)
    prepared_image = __import__("letsaigc.ui_analysis.inpaint", fromlist=["resize_and_pad_image"]).resize_and_pad_image(
        canonical.crop(crop), resize=resize, pad=pad, resample=Image.Resampling.LANCZOS
    )
    prepared_mask = __import__("letsaigc.ui_analysis.inpaint", fromlist=["prepare_edit_mask"]).prepare_edit_mask(
        canonical_mask.crop(crop),
        channel="red",
        polarity="white_is_edit",
        resize=resize,
        pad=pad,
    )

    canonical_ref = store.put(
        task_id, "safety", _png(canonical), role="canonical", media_type="image/png", source_ids=source_ids
    )
    canonical_mask_ref = store.put(
        task_id, "safety", _png(canonical_mask), role="edit_mask", media_type="image/png", source_ids=source_ids
    )
    image_ref = store.put(
        task_id, "safety", _png(prepared_image), role="image", media_type="image/png", source_ids=source_ids
    )
    mask_ref = store.put(
        task_id, "safety", _png(prepared_mask), role="edit_mask", media_type="image/png", source_ids=source_ids
    )
    layout_ref = _put_json(
        store,
        task_id,
        "safety",
        {
            "schema_version": 2,
            "task_id": task_id,
            "source_id": "source-1",
            "width": 16,
            "height": 12,
            "elements": [
                {
                    "element_id": "target",
                    "base_type": "container",
                    "semantic_tags": ["panel"],
                    "bbox": [2, 2, 8, 8],
                },
                {
                    "element_id": "keep",
                    "base_type": "image",
                    "semantic_tags": ["icon"],
                    "bbox": [8, 2, 10, 10],
                },
                {
                    "element_id": "outside",
                    "base_type": "text",
                    "semantic_tags": [],
                    "bbox": [12, 2, 14, 10],
                },
            ],
            "texts": [],
            "user_declared": "",
        },
        "review_layout",
        source_ids=source_ids,
    )
    # Keep this fixture aligned with the strict confirmed review DTO; the
    # compiler must not accept a hand-shaped layout that merely has elements.
    ReviewDocument.model_validate(json.loads(store.read(layout_ref).decode("utf-8")))
    selection_ref = _put_json(
        store,
        task_id,
        "safety",
        {
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "source-1",
                    "original_sha256": original_ref.sha256,
                    "layout_ref": layout_ref.model_dump(mode="json"),
                    "target_regions": [{"kind": "element", "element_id": "target"}],
                    "keep_elements": ["keep"],
                    "remove_elements": [],
                }
            ],
        },
        "selection",
        source_ids=source_ids,
    )
    transform_ref = _put_json(
        store,
        task_id,
        "safety",
        {
            "schema_version": 1,
            "canonical_size": [16, 12],
            "model_size": [8, 8],
            "crop": list(crop),
            "resize": list(resize),
            "pad": list(pad),
            "inverse_scale": [8 / 6, 8 / 6],
            "inverse": [[8 / 6, 0.0, 2 - 1 * 8 / 6], [0.0, 8 / 6, 2 - 1 * 8 / 6], [0.0, 0.0, 1.0]],
        },
        "view_transform",
        source_ids=source_ids,
    )

    raw = json.loads(
        (Path(__file__).resolve().parents[2] / "tests/fixtures/contracts/ui-edit-contracts.json").read_text(
            encoding="utf-8"
        )
    )["masked_plan"]
    raw = copy.deepcopy(raw)
    raw.update({"task_id": task_id, "session_id": session_id})
    raw["parameters"] = {
        "prompt": "restore the selected panel",
        "negative_prompt": "text, watermark",
        "seed": 7,
        "steps": 25,
        "cfg": 6.5,
        "denoise": 0.6,
    }
    raw["envelope"].update({"max_width": 64, "max_height": 48, "max_steps": 60})
    binding = raw["image_mask"]
    refs = {
        "original_ref": original_ref,
        "image_ref": image_ref,
        "mask_ref": mask_ref,
        "canonical_ref": canonical_ref,
        "canonical_edit_mask_ref": canonical_mask_ref,
        "selection_ref": selection_ref,
        "view_transform_ref": transform_ref,
        "layout_ref": layout_ref,
    }
    for key, ref in refs.items():
        if key not in {"original_ref", "layout_ref"}:
            binding[key] = ref.model_dump(mode="json")
    binding.update(
        {
            "selection_hash": selection_ref.sha256,
            "width": 8,
            "height": 8,
            "canonical_width": 16,
            "canonical_height": 12,
            "crop": list(crop),
            "resize": list(resize),
            "pad": list(pad),
        }
    )
    return MaskedGenerationPlan.model_validate(raw), store, refs, canonical, canonical_mask


def _trusted_sources(refs):
    return {
        "source-1": TrustedSource(
            "source-1",
            refs["original_ref"],
            width=16,
            height=12,
            layout_ref=refs["layout_ref"],
            canonical_ref=refs["canonical_ref"],
        )
    }


def _compile(
    plan,
    store,
    tmp_path: Path,
    trusted_sources,
    *,
    image_handle="prepared/image.png",
    mask_handle="prepared/mask.png",
):
    return WorkflowCompiler(AgentStore(tmp_path / "compiled")).compile(
        plan,
        uploaded_images=[image_handle],
        uploaded_mask=mask_handle,
        trusted_artifacts=store,
        trusted_sources=trusted_sources,
    )


def test_padded_partial_view_uses_exact_prepared_pair_and_remaps_only_crop(tmp_path: Path) -> None:
    plan, store, refs, canonical, canonical_mask = _fixture(tmp_path)
    compiled = _compile(plan, store, tmp_path, _trusted_sources(refs))
    assert compiled.validation["masked"] is True
    assert compiled.graph["10"]["inputs"]["image"] == "prepared/image.png"
    assert compiled.graph["12"]["inputs"]["image"] == "prepared/mask.png"

    generated = Image.new("RGB", (8, 8), (240, 10, 20))
    restored = restore_to_canonical(canonical, generated, canonical_mask, plan.image_mask)
    for y in range(canonical.height):
        for x in range(canonical.width):
            if canonical_mask.getpixel((x, y)):
                assert restored.getpixel((x, y)) == (240, 10, 20)
            else:
                assert restored.getpixel((x, y)) == canonical.getpixel((x, y))

    # The compiler must consume the bytes corresponding to the frozen crop /
    # resize / pad, rather than accepting an independently rendered view.
    expected = store.read(refs["image_ref"])
    forged = Image.new("RGB", (8, 8), (1, 2, 3))
    forged_ref = store.put(plan.task_id, "forged", _png(forged), role="image", media_type="image/png")
    forged_plan = plan.model_copy(update={"image_mask": plan.image_mask.model_copy(update={"image_ref": forged_ref})})
    assert expected != store.read(forged_ref)
    with pytest.raises((ValidationError, ValueError)):
        _compile(forged_plan, store, tmp_path, _trusted_sources(refs))


def test_transform_schema_version_tampering_is_rejected(tmp_path: Path) -> None:
    plan, store, refs, *_ = _fixture(tmp_path)
    transform = json.loads(store.read(refs["view_transform_ref"]).decode("utf-8"))
    transform["schema_version"] = 999
    forged_ref = _put_json(store, plan.task_id, "forged-transform", transform, "view_transform")
    forged_plan = plan.model_copy(
        update={"image_mask": plan.image_mask.model_copy(update={"view_transform_ref": forged_ref})}
    )
    with pytest.raises((ValidationError, ValueError)):
        _compile(forged_plan, store, tmp_path, _trusted_sources(refs))


def test_padded_mask_cannot_edit_a_frozen_keep_element(tmp_path: Path) -> None:
    canonical_mask = Image.new("L", (16, 12), 0)
    for y in range(2, 8):
        for x in range(2, 8):
            canonical_mask.putpixel((x, y), 255)
    canonical_mask.putpixel((8, 4), 255)  # inside the frozen keep element
    plan, store, refs, *_ = _fixture(tmp_path, canonical_mask=canonical_mask)
    with pytest.raises((ValidationError, ValueError)):
        _compile(plan, store, tmp_path, _trusted_sources(refs))


def test_same_task_wrong_canonical_is_rejected_by_trusted_source_binding(tmp_path: Path) -> None:
    plan, store, refs, canonical, canonical_mask = _fixture(tmp_path)
    wrong = Image.new("RGB", canonical.size, (3, 91, 211))
    wrong_mask = canonical_mask.copy()
    _, wrong_store, wrong_refs, _, _ = _fixture(
        tmp_path / "wrong", canonical=wrong, canonical_mask=wrong_mask
    )
    wrong_bound_refs = {
        key: store.put(
            plan.task_id,
            "wrong-source",
            wrong_store.read(ref),
            role=ref.role,
            media_type=ref.media_type,
            source_ids=ref.source_ids,
        )
        for key, ref in wrong_refs.items()
        if key != "original_ref"
    }
    # The selection remains valid for the trusted original/canonical/layout;
    # replacing only the binding's canonical-side artifacts must be rejected
    # by the trusted source mapping.
    binding = plan.image_mask.model_copy(
        update={
            "image_ref": wrong_bound_refs["image_ref"],
            "mask_ref": wrong_bound_refs["mask_ref"],
            "canonical_ref": wrong_bound_refs["canonical_ref"],
            "canonical_edit_mask_ref": wrong_bound_refs["canonical_edit_mask_ref"],
            "view_transform_ref": wrong_bound_refs["view_transform_ref"],
        }
    )
    forged_plan = plan.model_copy(update={"image_mask": binding})
    assert forged_plan.image_mask.canonical_ref.sha256 != plan.image_mask.canonical_ref.sha256
    with pytest.raises((ValidationError, ValueError)):
        _compile(forged_plan, store, tmp_path, _trusted_sources(refs))


def test_remove_element_outside_target_union_is_rejected(tmp_path: Path) -> None:
    plan, store, refs, *_ = _fixture(tmp_path)
    selection = json.loads(store.read(refs["selection_ref"]).decode("utf-8"))
    selection["sources"][0]["remove_elements"] = ["outside"]
    forged_ref = _put_json(store, plan.task_id, "forged-selection", selection, "selection")
    forged_plan = plan.model_copy(
        update={
            "image_mask": plan.image_mask.model_copy(
                update={"selection_ref": forged_ref, "selection_hash": forged_ref.sha256}
            )
        }
    )
    with pytest.raises((ValidationError, ValueError)):
        _compile(forged_plan, store, tmp_path, _trusted_sources(refs))


def test_new_in_range_fixed_parameters_change_frozen_graph(tmp_path: Path) -> None:
    plan, store, refs, *_ = _fixture(tmp_path)
    baseline = _compile(plan, store, tmp_path, _trusted_sources(refs))
    parameters = {**plan.parameters, "steps": 26}
    approved = plan.model_copy(update={"parameters": parameters})
    compiled = _compile(approved, store, tmp_path, _trusted_sources(refs))
    assert compiled.graph["3"]["inputs"]["steps"] == 26
    assert compiled.graph_sha256 != baseline.graph_sha256


def test_backend_uploads_only_the_verified_prepared_image_and_mask(
    monkeypatch, tmp_path: Path
) -> None:
    """The provider receives the two verified prepared artifacts, never canonical bytes."""

    plan, store, refs, *_ = _fixture(tmp_path)
    backend_module = __import__("letsaigc.backends.comfy", fromlist=["ComfyBackend"])
    uploaded: list[tuple[bytes, str, str]] = []

    class Client:
        def upload_image(self, path: Path, *, subfolder: str) -> str:
            uploaded.append((path.read_bytes(), path.name, subfolder))
            return f"{subfolder}/{path.name}"

        def object_info(self):
            return None

    model = SimpleNamespace(
        id="sdxl-base-1.0",
        profiles=["fixture"],
        runtime_status="qualified",
        license=SimpleNamespace(lane="local_gpu"),
    )
    catalog = SimpleNamespace(models=[model], by_id=lambda: {model.id: model})

    class ModelManager:
        def __init__(self, catalog=None):
            self.catalog = catalog

        def verify_model(self, model):
            return [{"ok": True, "sha256": "a" * 64}]

    def fake_run(command, **kwargs):
        if len(command) >= 2 and command[1] == "show":
            relative = command[2].split("HEAD:", 1)[1]
            return SimpleNamespace(
                returncode=0,
                stdout=(Path(__file__).resolve().parents[2] / relative).read_bytes(),
            )
        return SimpleNamespace(returncode=0, stdout="fixture-commit\n")

    monkeypatch.setattr(backend_module, "load_catalog", lambda: catalog)
    monkeypatch.setattr(backend_module, "ModelManager", ModelManager)
    monkeypatch.setattr(backend_module, "assert_model_allowed", lambda model, operation: None)
    monkeypatch.setattr(
        backend_module,
        "local_path",
        lambda *parts: tmp_path.joinpath("runtime", *parts),
    )
    monkeypatch.setattr(
        backend_module.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=16 * 1024**3),
    )
    monkeypatch.setattr(
        backend_module.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=32 * 1024**3),
    )
    monkeypatch.setattr(backend_module.subprocess, "run", fake_run)
    monkeypatch.setattr(backend_module, "create_manifest", lambda **kwargs: _FakeManifest())
    monkeypatch.setattr(backend_module, "save_manifest", lambda manifest: None)

    compiler = WorkflowCompiler(AgentStore(tmp_path / "compiled"))
    backend = backend_module.ComfyBackend(
        client=Client(),
        compiler=compiler,
        artifact_store=store,
        trusted_sources=_trusted_sources(refs),
    )
    compiled, _, _ = backend.prepare(plan, iteration_id="i0")

    assert [item[0] for item in uploaded] == [
        store.read(refs["image_ref"]),
        store.read(refs["mask_ref"]),
    ]
    assert uploaded[0][1].startswith("image-")
    assert uploaded[1][1].startswith("mask-")
    assert uploaded[0][2] == uploaded[1][2] == f"letsaigc-agent/{plan.session_id}/{plan.task_id}"
    assert compiled.graph["10"]["inputs"]["image"].endswith(uploaded[0][1])
    assert compiled.graph["12"]["inputs"]["image"].endswith(uploaded[1][1])


@dataclass
class _FakeManifest:
    run_id: str = "inference-safety"
    source: dict = field(default_factory=dict)
    tracking: dict = field(default_factory=dict)
    outputs: list = field(default_factory=list)
    governance: object = field(default_factory=lambda: SimpleNamespace(validations={}))
    status: str = "validated"


def test_zero_mask_execute_does_not_submit_to_provider(monkeypatch, tmp_path: Path) -> None:
    plan, store, refs, canonical, _ = _fixture(tmp_path, canonical_mask=Image.new("L", (16, 12), 0))
    backend_module = __import__("letsaigc.backends.comfy", fromlist=["ComfyBackend", "save_manifest"])
    submitted: list[dict] = []

    class Client:
        def submit(self, graph):
            submitted.append(graph)
            return "unexpected-provider-request"

    compiled = CompiledWorkflow(
        recipe_id="sdxl-inpaint",
        recipe_version="1.0.0",
        graph={},
        graph_sha256="0" * 64,
        models=["sdxl-base-1.0"],
        outputs=[],
        local_graph_path="/tmp/empty-graph.json",
        local_contract_path="/tmp/empty-contract.json",
        contract_sha256="1" * 64,
        validation={"no_edit_pixels": True},
    )
    manifest = _FakeManifest()
    backend = backend_module.ComfyBackend(client=Client(), artifact_store=store)
    monkeypatch.setattr(backend, "prepare", lambda plan, iteration_id: (compiled, manifest, None))
    monkeypatch.setattr(backend_module, "save_manifest", lambda value: None)
    result = backend.execute(plan, iteration_id="i0")
    assert submitted == []
    assert result.request_id is None
    assert result.usage == {"no_edit_pixels": True}
    assert manifest.status == "succeeded"
