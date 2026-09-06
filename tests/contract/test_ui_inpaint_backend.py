"""Offline backend contracts for masked output materialization."""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from letsaigc.backends.comfy import ComfyBackend
from letsaigc.errors import RuntimeExecutionError
from letsaigc.schemas.agent import CompiledWorkflow
from letsaigc.schemas.pipeline import ArtifactRef
from letsaigc.tracking.manifest import create_manifest

_HELPER_SPEC = importlib.util.spec_from_file_location(
    "ui_inpaint_compile_helpers", Path(__file__).with_name("test_ui_inpaint_compile.py")
)
assert _HELPER_SPEC and _HELPER_SPEC.loader
_HELPERS = importlib.util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_HELPERS)
_masked_plan = _HELPERS._masked_plan
_trusted_sources = _HELPERS._trusted_sources


def _png(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


class _Sampler:
    def start(self) -> None:
        return None

    def stop(self) -> dict:
        return {}


def _compiled() -> CompiledWorkflow:
    return CompiledWorkflow(
        recipe_id="sdxl-inpaint",
        recipe_version="1.0.0",
        graph={"3": {"class_type": "KSampler", "inputs": {}}},
        graph_sha256="0" * 64,
        models=["sdxl-base-1.0"],
        outputs=[
            {
                "node_id": "9",
                "history_field": "images",
                "role": "primary_image",
                "media_kind": "image",
                "allowed_extensions": [".png"],
            }
        ],
        local_graph_path="graph.json",
        local_contract_path="graph.contract.json",
        contract_sha256="1" * 64,
        validation={"masked": True},
    )


def _run_backend(monkeypatch, tmp_path: Path, candidate: Image.Image | bytes):
    plan, store, *_ = _masked_plan(tmp_path / "artifacts")
    output_root = tmp_path / "output"
    output_root.mkdir(parents=True)
    candidate_path = output_root / "candidate.png"
    if isinstance(candidate, bytes):
        candidate_path.write_bytes(candidate)
    else:
        candidate.save(candidate_path, format="PNG")

    import letsaigc.backends.comfy as backend_module

    class Client:
        def submit(self, graph):
            return "prompt-1"

        def wait(self, prompt_id, *, timeout_seconds):
            return {"outputs": {"9": {"images": [{"filename": candidate_path.name, "subfolder": ""}]}}}

    manifest = create_manifest(kind="inference", parameters={}, license_lanes=[], source={})
    manifest.status = "validated"
    backend = ComfyBackend(
        client=Client(), artifact_store=store, trusted_sources=_trusted_sources(plan, store)
    )
    monkeypatch.setattr(backend, "prepare", lambda *_args, **_kwargs: (
        _compiled(), manifest, SimpleNamespace(resource_budget={"timeout_seconds": 5})
    ))
    monkeypatch.setattr(backend_module, "local_path", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(backend_module, "GpuMemorySampler", _Sampler)
    monkeypatch.setattr(backend_module, "probe_media", lambda _path: None)
    monkeypatch.setattr(backend_module, "save_manifest", lambda _manifest: None)
    try:
        result = backend.execute(plan, iteration_id="i0")
    except RuntimeExecutionError as exc:
        result = exc
    return result, manifest, candidate_path, plan, store


def test_masked_candidate_is_remapped_to_canonical_output(monkeypatch, tmp_path: Path) -> None:
    candidate = Image.new("RGB", (8, 8), (240, 10, 20))
    result, manifest, _, plan, store = _run_backend(monkeypatch, tmp_path, candidate)

    assert len(result.outputs) == 1
    output = Image.open(result.outputs[0])
    assert output.size == (8, 6)
    assert output.mode == "RGB"
    assert manifest.source["masked_candidate"]["sha256"]
    assert manifest.source["masked_composite"]["candidate_sha256"]
    assert manifest.outputs[0].role == "primary_image"
    assert output.getpixel((3, 2)) == (240, 10, 20)
    assert output.getpixel((0, 0)) != (240, 10, 20)
    canonical = Image.open(io.BytesIO(store.read(plan.image_mask.canonical_ref))).convert("RGB")
    canonical_mask = Image.open(io.BytesIO(store.read(plan.image_mask.canonical_edit_mask_ref))).convert("L")
    for y in range(canonical.height):
        for x in range(canonical.width):
            if canonical_mask.getpixel((x, y)) == 0:
                assert output.getpixel((x, y)) == canonical.getpixel((x, y))


def test_wrong_size_candidate_keeps_failure_evidence(monkeypatch, tmp_path: Path) -> None:
    candidate = Image.new("RGB", (7, 8), (240, 10, 20))
    result, manifest, candidate_path, _, _ = _run_backend(monkeypatch, tmp_path, candidate)
    assert isinstance(result, RuntimeExecutionError)
    assert manifest.status == "failed"
    assert manifest.source["masked_candidate"]["path"] == str(candidate_path.resolve())
    assert manifest.source["masked_candidate"]["width"] == 7


def test_invalid_png_candidate_is_saved_as_immutable_evidence_before_decode(
    monkeypatch, tmp_path: Path
) -> None:
    candidate_bytes = b"candidate bytes that are not png"
    result, manifest, _, _, store = _run_backend(monkeypatch, tmp_path, candidate_bytes)
    assert isinstance(result, RuntimeExecutionError)
    evidence = manifest.source["masked_candidate"]
    candidate_ref = ArtifactRef.model_validate(evidence["artifact_ref"])
    assert candidate_ref.role == "inpaint_candidate"
    assert store.read(candidate_ref) == candidate_bytes
    assert evidence["sha256"] == candidate_ref.sha256
    assert evidence["size_bytes"] == len(candidate_bytes)
    assert "width" not in evidence


def test_empty_mask_returns_canonical_noop_snapshot_without_submit(monkeypatch, tmp_path: Path) -> None:
    plan, store, *_ = _masked_plan(tmp_path / "empty-artifacts")
    empty_canonical_mask = Image.new("L", (8, 6), 0)
    empty_prepared_mask = Image.new("L", (8, 8), 0)
    canonical_mask_ref = store.put(
        plan.task_id,
        "empty",
        _png(empty_canonical_mask),
        role="edit_mask",
        media_type="image/png",
        source_ids=["source-1"],
    )
    mask_ref = store.put(
        plan.task_id,
        "empty",
        _png(empty_prepared_mask),
        role="edit_mask",
        media_type="image/png",
        source_ids=["source-1"],
    )
    plan = plan.model_copy(update={
        "image_mask": plan.image_mask.model_copy(update={
            "mask_ref": mask_ref,
            "canonical_edit_mask_ref": canonical_mask_ref,
        })
    })
    import letsaigc.backends.comfy as backend_module

    class Client:
        def submit(self, _graph):
            raise AssertionError("empty mask must not submit")

    compiled = _compiled().model_copy(update={"graph": {}, "outputs": [], "validation": {"no_edit_pixels": True}})
    manifest = create_manifest(kind="inference", parameters={}, license_lanes=[], source={})
    manifest.status = "validated"
    backend = ComfyBackend(
        client=Client(), artifact_store=store, trusted_sources=_trusted_sources(plan, store)
    )
    monkeypatch.setattr(backend, "prepare", lambda *_args, **_kwargs: (
        compiled, manifest, SimpleNamespace(resource_budget={"timeout_seconds": 5})
    ))
    monkeypatch.setattr(backend_module, "local_path", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(backend_module, "probe_media", lambda _path: None)
    monkeypatch.setattr(backend_module, "save_manifest", lambda _manifest: None)

    result = backend.execute(plan, iteration_id="empty")

    assert len(result.outputs) == 1
    assert Image.open(result.outputs[0]).size == (8, 6)
    assert result.request_id is None
    assert manifest.source["masked_noop"]["canonical_sha256"] == plan.image_mask.canonical_ref.sha256
