"""T022 contracts for the isolated SAM2 service boundary.

These tests intentionally exercise only the model/runtime boundary.  Prompt
interpretation, mask construction and alpha/glyph extraction belong to T023.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import weakref
from functools import lru_cache, wraps
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef, Cost, canonical_json, digest
from letsaigc.schemas.ui import UIAnalysisRequest, UIResourceLimits, UIStepBinding
from letsaigc.vision.base import SAMJob, issue_sam_permit
from letsaigc.vision.client import SAMOperationBackend, VisionClient
from letsaigc.vision.sam_loader import (
    SAM2Engine,
    load_sam_settings,
    prepare_sam_view,
    validate_loaded_sam_preprocessing,
    validate_sam_model_files,
    validate_sam_package_lock,
    validate_sam_preprocessing,
)
from letsaigc.vision.service import VisionApplication, VisionJobs, validation_runtime_paths

ROOT = Path(__file__).parents[2]


def test_sam_engine_release_synchronizes_collects_cycles_and_proves_zero_cuda_bytes():
    calls = []

    class CUDA:
        def synchronize(self):
            calls.append("synchronize")

        def empty_cache(self):
            calls.append("empty_cache")

        def ipc_collect(self):
            calls.append("ipc_collect")

        def memory_allocated(self):
            calls.append("memory_allocated")
            return 0

    class Cycle:
        def __init__(self):
            self.self_reference = self

    engine = SAM2Engine.__new__(SAM2Engine)
    engine._torch = SimpleNamespace(cuda=CUDA())
    engine.model = Cycle()
    engine.processor = Cycle()
    engine.loader_ready = True
    engine.execution_ready = True
    model_reference = weakref.ref(engine.model)
    processor_reference = weakref.ref(engine.processor)

    assert engine.release() is True
    assert model_reference() is None
    assert processor_reference() is None
    assert engine.model is None
    assert engine.processor is None
    assert engine.loader_ready is False
    assert engine.execution_ready is False
    assert engine.released_cuda_allocated_bytes == 0
    assert calls == [
        "memory_allocated",
        "memory_allocated",
        "synchronize",
        "memory_allocated",
        "memory_allocated",
        "memory_allocated",
        "empty_cache",
        "ipc_collect",
        "empty_cache",
        "memory_allocated",
    ]


def test_sam_engine_release_clears_hidden_transformers_method_cache():
    calls = []

    class CUDA:
        def synchronize(self):
            calls.append("synchronize")

        def empty_cache(self):
            calls.append("empty_cache")

        def ipc_collect(self):
            calls.append("ipc_collect")

        def memory_allocated(self):
            return 0

    class CachedModule:
        pass

    def forward(self, value):
        return value

    cached_forward = lru_cache(maxsize=1)(forward)

    @wraps(forward)
    def hidden_cache_wrapper(*args, **kwargs):
        return cached_forward(*args, **kwargs)

    CachedModule.forward = hidden_cache_wrapper
    module = CachedModule()

    class Model:
        def modules(self):
            return [module]

        def to(self, device):
            calls.append("to:" + device)
            return self

    class Allocation:
        pass

    allocation = Allocation()
    allocation_reference = weakref.ref(allocation)
    assert module.forward(allocation) is allocation
    del allocation
    assert allocation_reference() is not None

    engine = SAM2Engine.__new__(SAM2Engine)
    engine._torch = SimpleNamespace(
        cuda=CUDA(),
        _C=SimpleNamespace(
            _cuda_clearCublasWorkspaces=lambda: calls.append("clear_cublas")
        ),
    )
    engine.model = Model()
    engine.processor = object()
    engine.loader_ready = True
    engine.execution_ready = True

    assert engine.release() is True
    assert engine.released_runtime_caches >= 1
    assert allocation_reference() is None
    assert calls == [
        "synchronize",
        "to:cpu",
        "synchronize",
        "clear_cublas",
        "synchronize",
        "empty_cache",
        "ipc_collect",
        "empty_cache",
    ]


def _complete_snapshot(lock: dict, root: Path, *, payload: bytes = b"verified") -> dict:
    """Create a complete synthetic snapshot with explicit companion hashes."""

    local_lock = json.loads(json.dumps(lock))
    model = local_lock["segmentation"]["model"]
    model["directory"] = "weights"
    model_dir = root / "weights"
    model_dir.mkdir(parents=True, exist_ok=True)
    names = ["model.safetensors", *model["required_processor_files"]]
    files = {}
    for name in names:
        body = payload if name == "model.safetensors" else (b"{}" if name.endswith(".json") else payload)
        (model_dir / name).write_bytes(body)
        files[name] = {
            "sha256": hashlib.sha256(body).hexdigest(),
            "format": "safetensors" if name.endswith(".safetensors") else "json",
        }
    model["files"] = files
    return local_lock


def _sam_job(ui_store, *, task_id="sam-task", operation_id="op-sam"):
    canonical = ui_store.put(task_id, "input", b"canonical", role="canonical", media_type="image/png")
    selection = ui_store.put(task_id, "selection", b"selection", role="selection")
    snapshot = ui_store.put(task_id, "snapshot", b"snapshot", role="model_snapshot")
    return SAMJob(
        task_id=task_id,
        operation_id=operation_id,
        canonical_ref=canonical,
        selection_ref=selection,
        selection_revision=3,
        selection_hash=selection.sha256,
        model_snapshot_ref=snapshot,
        model_digest=snapshot.sha256,
        prompts=[
            {
                "element_id": "element-1",
                "box": [10, 20, 100, 120],
                "points": [[40.0, 50.0]],
            }
        ],
        parameters_hash=digest({"prompt_version": "sam-prompt-v1"}),
        resources={"vram_bytes": 1024 * 1024 * 1024},
    )


def test_segmentation_environment_and_lock_are_pinned_and_local_only():
    environment = yaml.safe_load((ROOT / "environment/vision-segmentation.yml").read_text())
    assert environment["name"] == "letsaigc-vision-segmentation"
    assert environment["dependencies"][0] == "python=3.12"
    pip_lines = environment["dependencies"][-1]["pip"]
    assert "transformers==4.57.6" in pip_lines
    assert "torch==2.9.1" in pip_lines
    assert "torchvision==0.24.1" in pip_lines

    config, lock = load_sam_settings(ROOT)
    assert config["endpoint"] == "http://127.0.0.1:8767"
    model = lock["segmentation"]["model"]
    assert model["repo"] == "facebook/sam2.1-hiera-large"
    assert model["revision"] == "665f8e2ad61cf5f53d65644ff27c8ee525124610"
    assert model["format"] == "safetensors"
    assert model["files"]["model.safetensors"]["sha256"] == (
        "dc407dce21301fd94abb395c5099b4f2c455fdc8a8f261ac3d0ea6d4cd197230"
    )
    assert model["license"]["id"] == "Apache-2.0"
    assert model["license"]["status"] == "verified"
    assert ".pt" not in json.dumps(model).lower()
    assert lock["segmentation"]["loader"] == "transformers-sam2-safetensors"
    assert lock["segmentation"]["local_files_only"] is True
    assert lock["segmentation"]["trust_remote_code"] is False
    evidence = yaml.safe_load(
        (ROOT / "configs/runtime/vision-sam2-source-evidence.yaml").read_text(encoding="utf-8")
    )
    assert evidence["status"] == "verified"
    assert evidence["model"]["safetensors"]["sha256"] == model["files"]["model.safetensors"]["sha256"]
    assert evidence["model"]["processor_companion_hashes"]["status"] == "verified"
    for name in model["required_processor_files"]:
        assert evidence["model"]["processor_companion_hashes"]["files"][name] == model["files"][name]["sha256"]
    assert evidence["model"]["license"]["status"] == "verified"
    assert evidence["model"]["license"]["source"].endswith(
        "/facebook/sam2.1-hiera-large/blob/665f8e2ad61cf5f53d65644ff27c8ee525124610/README.md"
    )
    assert evidence["preprocessing"]["transformers_processor_source"].endswith(
        "/transformers/v4.57.6/src/transformers/models/sam2/processing_sam2.py"
    )


def test_windows_sam_package_overlay_is_exact_and_verified(monkeypatch):
    monkeypatch.setattr("letsaigc.vision.sam_loader.platform.system", lambda: "Windows")
    monkeypatch.setattr("letsaigc.vision.sam_loader.platform.machine", lambda: "AMD64")
    _, lock = load_sam_settings(ROOT)
    segmentation = lock["segmentation"]
    assert segmentation["platform"] == "Windows-AMD64"
    assert segmentation["verification"] == "verified"
    assert segmentation["packages"]["torch"] == {
        "version": "2.9.1+cu130",
        "wheel": "torch-2.9.1+cu130-cp312-cp312-win_amd64.whl",
        "wheel_sha256": "cd3232a562ad2a2699d48130255e1b24c07dfe694a40dcd24fad683c752de121",
        "source": "https://download.pytorch.org/whl/cu130/torch/",
    }
    assert segmentation["packages"]["torchvision"] == {
        "version": "0.24.1+cu130",
        "wheel": "torchvision-0.24.1+cu130-cp312-cp312-win_amd64.whl",
        "wheel_sha256": "d31ceaded0d9b737471fa680ccd9e1acb6d5f0f70f03ef3a8d786a99c79da7cf",
        "source": "https://download.pytorch.org/whl/cu130/torchvision/",
    }


def test_sam_package_lock_rejects_unverified_or_mismatched_runtime(monkeypatch):
    monkeypatch.setattr("letsaigc.vision.sam_loader.platform.system", lambda: "Windows")
    monkeypatch.setattr("letsaigc.vision.sam_loader.platform.machine", lambda: "AMD64")
    _, lock = load_sam_settings(ROOT)
    versions = {name: value["version"] for name, value in lock["segmentation"]["packages"].items()}
    monkeypatch.setattr(
        "letsaigc.vision.sam_loader.importlib.metadata.version", lambda name: versions[name]
    )
    validate_sam_package_lock(lock)
    pending = json.loads(json.dumps(lock))
    pending["segmentation"]["verification"] = "pending_platform_verification"
    with pytest.raises(PipelineError) as raised:
        validate_sam_package_lock(pending)
    assert raised.value.code == "model_not_ready"
    versions["torch"] = "2.9.1"
    with pytest.raises(PipelineError) as raised:
        validate_sam_package_lock(lock)
    assert raised.value.code == "model_not_ready"


def test_sam_model_files_require_the_exact_local_safetensors_snapshot(tmp_path):
    lock = load_sam_settings(ROOT)[1]
    with pytest.raises(PipelineError) as raised:
        validate_sam_model_files(lock, tmp_path)
    assert raised.value.code == "model_not_ready"

    model_dir = tmp_path / "weights"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(b"wrong")
    local_lock = json.loads(json.dumps(lock))
    local_lock["segmentation"]["model"]["directory"] = "weights"
    with pytest.raises(PipelineError) as raised:
        validate_sam_model_files(local_lock, tmp_path)
    assert raised.value.code == "model_not_ready"

    payload = b"verified synthetic safetensors placeholder"
    (model_dir / "model.safetensors").write_bytes(payload)
    local_lock["segmentation"]["model"]["files"]["model.safetensors"]["sha256"] = hashlib.sha256(
        payload
    ).hexdigest()
    # A correct weight hash alone is insufficient: every processor/config
    # companion used by Transformers must be present and independently hashed.
    with pytest.raises(PipelineError) as raised:
        validate_sam_model_files(local_lock, tmp_path)
    assert raised.value.code == "model_not_ready"

    complete = _complete_snapshot(lock, tmp_path)
    assert validate_sam_model_files(complete, tmp_path) == model_dir.resolve()

    (model_dir / "preprocessor_config.json").unlink()
    with pytest.raises(PipelineError) as raised:
        validate_sam_model_files(complete, tmp_path)
    assert raised.value.code == "model_not_ready"

    complete = _complete_snapshot(lock, tmp_path, payload=b"verified-again")
    (model_dir / "model.safetensors").write_bytes(b"tampered")
    with pytest.raises(PipelineError) as raised:
        validate_sam_model_files(complete, tmp_path)
    assert raised.value.code == "model_not_ready"

    complete = _complete_snapshot(lock, tmp_path, payload=b"verified-third")
    (model_dir / "legacy.pt").write_bytes(b"pickle")
    with pytest.raises(PipelineError) as raised:
        validate_sam_model_files(complete, tmp_path)
    assert raised.value.code == "model_not_ready"


def test_sam_loader_forces_local_safetensors_and_rejects_key_drift(tmp_path, monkeypatch):
    lock = load_sam_settings(ROOT)[1]
    lock = _complete_snapshot(lock, tmp_path)
    monkeypatch.setattr("letsaigc.vision.sam_loader.validate_sam_package_lock", lambda _lock: None)

    calls = []
    cuda_moves = []

    class FakeModel:
        def to(self, device):
            assert device in {"cuda", "cpu"}
            cuda_moves.append(device)
            return self

        def eval(self):
            return self

    class FakeSam:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            calls.append((args, kwargs))
            return FakeModel(), {"missing_keys": [], "mismatched_keys": [], "unexpected_keys": []}

    class FakeProcessor:
        def __init__(self, *, size=None):
            self.image_processor = SimpleNamespace(
                size=size or {"height": 1024, "width": 1024},
                default_to_square=True,
                do_pad=None,
                image_processor_type="Sam2ImageProcessorFast",
            )
            self.target_size = 1024
            self.processor_class = "Sam2Processor"

        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            calls.append((args, kwargs))
            return cls()

    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(
            synchronize=lambda: None,
            empty_cache=lambda: None,
            ipc_collect=lambda: None,
            memory_allocated=lambda: 0,
        )
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(Sam2Model=FakeSam, Sam2Processor=FakeProcessor))
    engine = SAM2Engine(lock, tmp_path)
    assert engine.loader_ready is True
    assert engine.execution_ready is True
    assert calls[0][1]["local_files_only"] is True
    assert calls[0][1]["trust_remote_code"] is False
    assert calls[1][1]["local_files_only"] is True
    assert calls[1][1]["use_safetensors"] is True
    assert calls[1][1]["trust_remote_code"] is False
    assert calls[1][1]["output_loading_info"] is True
    assert engine.release() is True
    assert cuda_moves == ["cuda", "cpu"]
    cuda_moves.clear()

    class DriftProcessor(FakeProcessor):
        def __init__(self):
            super().__init__(size={"height": 512, "width": 512})

    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(Sam2Model=FakeSam, Sam2Processor=DriftProcessor))
    with pytest.raises(PipelineError) as raised:
        SAM2Engine(lock, tmp_path)
    assert raised.value.code == "model_not_ready"
    assert cuda_moves == []

    class DriftSam(FakeSam):
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return FakeModel(), {"missing_keys": ["unexpected.model.key"], "mismatched_keys": [], "unexpected_keys": []}

    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(Sam2Model=DriftSam, Sam2Processor=FakeProcessor))
    with pytest.raises(PipelineError) as raised:
        SAM2Engine(lock, tmp_path)
    assert raised.value.code == "model_not_ready"

    class ErrorSam(FakeSam):
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return FakeModel(), {
                "missing_keys": [], "mismatched_keys": [], "unexpected_keys": [], "error_msgs": ["bad"]
            }

    monkeypatch.setitem(
        sys.modules, "transformers", SimpleNamespace(Sam2Model=ErrorSam, Sam2Processor=FakeProcessor)
    )
    with pytest.raises(PipelineError) as raised:
        SAM2Engine(lock, tmp_path)
    assert raised.value.code == "model_not_ready"

    class UnhashableDriftSam(FakeSam):
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return FakeModel(), {"missing_keys": [], "mismatched_keys": [["key", []]], "unexpected_keys": []}

    monkeypatch.setitem(
        sys.modules, "transformers", SimpleNamespace(Sam2Model=UnhashableDriftSam, Sam2Processor=FakeProcessor)
    )
    with pytest.raises(PipelineError) as raised:
        SAM2Engine(lock, tmp_path)
    assert raised.value.code == "model_not_ready"


def test_sam_health_distinguishes_loaded_model_from_t023_execution(tmp_path, ui_store):
    class LoadedOnly:
        loader_ready = True
        execution_ready = False

    app = VisionApplication(
        tmp_path, ui_store, token="local-auth", signing_key="x" * 32,
        engine=LoadedOnly(), model_digest="a" * 64, capability="segmentation"
    )
    code, body = app.dispatch("GET", "/v1/health", {"Authorization": "Bearer local-auth"}, b"")
    assert code == 200
    assert body["loader_ready"] is True
    assert body["ready"] is False
    assert body["reason"] == "segmentation_not_ready"


def test_segmentation_validation_root_is_confined_to_local_evidence(tmp_path):
    artifacts, provider = validation_runtime_paths(tmp_path, "t024-windows/acceptance-state")
    assert artifacts == (
        tmp_path / ".local/validation/ui-analysis/t024-windows/acceptance-state/artifacts"
    ).resolve()
    assert provider == (
        tmp_path / ".local/validation/ui-analysis/t024-windows/acceptance-state/provider"
    ).resolve()
    assert validation_runtime_paths(tmp_path, r"t024-windows\acceptance-state") == (artifacts, provider)
    for value in ("../escape", "C:/escape", "/escape", r"..\escape", r"\escape", "C:escape"):
        with pytest.raises(PipelineError) as raised:
            validation_runtime_paths(tmp_path, value)
        assert raised.value.code == "unsafe_path"


def test_sam_preprocessing_records_resize_pad_and_inverse_mapping():
    view = prepare_sam_view(2000, 1000, max_edge=1024)
    assert view["canonical_size"] == [2000, 1000]
    assert view["model_size"] == [1024, 1024]
    assert view["resize"] == [1024, 1024]
    assert view["pad"] == [0, 0, 0, 0]
    assert view["inverse_scale"] == [2000 / 1024, 1000 / 1024]
    assert view["processor"] == {
        "size": {"height": 1024, "width": 1024},
        "default_to_square": True,
        "do_pad": None,
        "target_size": 1024,
    }

    square = prepare_sam_view(801, 601, max_edge=1024)
    assert square["model_size"] == [1024, 1024]
    assert square["inverse"][0][0] > 0
    assert square["inverse"][1][1] > 0

    with pytest.raises(PipelineError):
        prepare_sam_view(0, 100)
    with pytest.raises(PipelineError):
        prepare_sam_view(100, 100, max_edge=0)
    with pytest.raises(PipelineError):
        prepare_sam_view(100, 100, pad_to=64)

    lock = load_sam_settings(ROOT)[1]
    validate_sam_preprocessing(lock)
    tampered = json.loads(json.dumps(lock))
    tampered["segmentation"]["model"]["preprocessing"]["size"]["width"] = 512
    with pytest.raises(PipelineError) as raised:
        validate_sam_preprocessing(tampered)
    assert raised.value.code == "model_not_ready"

    processor = SimpleNamespace(
        image_processor=SimpleNamespace(
            size={"height": 1024, "width": 1024},
            default_to_square=True,
            do_pad=None,
            image_processor_type="Sam2ImageProcessorFast",
        ),
        target_size=1024,
        processor_class="Sam2Processor",
    )
    validate_loaded_sam_preprocessing(processor, lock)
    processor.image_processor.default_to_square = False
    with pytest.raises(PipelineError) as raised:
        validate_loaded_sam_preprocessing(processor, lock)
    assert raised.value.code == "model_not_ready"


def test_sam_permit_binds_selection_model_input_and_parameters_without_secrets():
    class Operation:
        state = "submitting"
        task_id = "sam-task"

    class Binding:
        capability = "ui.segment"
        inputs = []
        dependency_hashes = {"sam-model": "a" * 64, "sam-parameters": digest({"prompt_version": "sam-prompt-v1"})}
        selection_ref = None
        selection_revision = 3
        selection_hash = None

    class Ledger:
        def get(self, operation_id):
            assert operation_id == "op-sam"
            return Operation()

        def ui_binding(self, operation_id):
            return Binding()

        def plan(self, task_id):
            return type(
                "Plan",
                (),
                {
                    "workflow_type": "ui_segmentation",
                    "parameters": {},
                    "envelope": type("Envelope", (), {"allowed_capabilities": ["ui.segment"]})(),
                },
            )()

    # Build refs without relying on a provider service for this permit-only test.
    from types import SimpleNamespace

    canonical = ArtifactRef(
        task_id="sam-task", artifact_id="canonical", key="sam-task/canonical", sha256="b" * 64,
        size_bytes=9, media_type="image/png", role="canonical", operation_id="op-sam"
    )
    selection = ArtifactRef(
        task_id="sam-task", artifact_id="selection", key="sam-task/selection", sha256="c" * 64,
        size_bytes=9, media_type="application/json", role="selection", operation_id="op-sam"
    )
    snapshot = ArtifactRef(
        task_id="sam-task", artifact_id="snapshot", key="sam-task/snapshot", sha256="d" * 64,
        size_bytes=8, media_type="application/json", role="model_snapshot", operation_id="op-sam"
    )
    job = SAMJob(
        task_id="sam-task", operation_id="op-sam", canonical_ref=canonical,
        selection_ref=selection, selection_revision=3, selection_hash=selection.sha256,
        model_snapshot_ref=snapshot, model_digest=snapshot.sha256,
        prompts=[{"element_id": "element-1", "box": [1, 2, 3, 4]}],
        parameters_hash=digest({"prompt_version": "sam-prompt-v1"}),
        resources={"vram_bytes": 1024},
    )
    # The binding's frozen selection and input references are checked by the
    # coordinator implementation; a missing binding match must not be signed.
    with pytest.raises(PipelineError):
        issue_sam_permit(Ledger(), SimpleNamespace(read=lambda ref: b"{}"), job, "s" * 32)


def test_sam_permit_uses_the_v5_ledger_resource_owner_and_frozen_child_request(tmp_path):
    """The permit must be backed by the real child/operation/resource tables."""
    budget = {
        "max_total_cost_usd": 1,
        "max_iteration_cost_usd": 0.6,
        "max_total_gpu_minutes": 10,
        "max_iteration_gpu_minutes": 5,
        "max_revisions": 2,
    }
    service = PipelineService(tmp_path, ui_schema=5)
    source = service.artifacts.put(
        "root", "input", b"image-bytes", role="original", media_type="image/png"
    )
    root = service.ui_plan(
        "root",
        UIAnalysisRequest(
            input={"kind": "manual", "inputs": [source]},
            output_mode="reconstruct",
            reconstruction_target="scene_background",
            selection_mode="deferred",
            budget=budget,
        ),
    )
    service.ledger.consume_approval(approve(service.ledger, root.task_id, root.fingerprint))
    selection_body = {
        "schema_version": 1,
        "sources": [{
            "source_id": source.artifact_id,
            "original_sha256": source.sha256,
            "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 10, 10]}],
            "keep_elements": [],
            "remove_elements": [],
        }],
    }
    root_selection = service.artifacts.put(
        "root", "selection", canonical_json(selection_body).encode(), role="selection"
    )
    canonical = service.artifacts.put("child", "input", b"image-bytes", role="canonical", media_type="image/png")
    local_selection = service.artifacts.put(
        "child", "input", canonical_json(selection_body).encode(), role="selection"
    )
    snapshot = service.artifacts.put("child", "model", b"snapshot", role="model_snapshot")
    prompt = {"element_id": "region-1", "box": [0, 0, 10, 10], "points": []}
    request = {
        "schema_version": 1,
        "canonical_ref": canonical.model_dump(mode="json"),
        "selection_ref": local_selection.model_dump(mode="json"),
        "selection_revision": 0,
        "selection_hash": local_selection.sha256,
        "prompts": [prompt],
        "model_snapshot_ref": snapshot.model_dump(mode="json"),
        "prompt_version": "sam-prompt-v1",
        "resources": UIResourceLimits().model_dump(mode="json"),
        "result_roles": ["mask"],
    }
    request_ref = service.artifacts.put("child", "request", canonical_json(request).encode(), role="request")
    plan = service.ui_child_plan(
        "child", parent_task_id="root", purpose="segmentation", source_ids=[source.artifact_id],
        request_ref=request_ref, selection_ref=root_selection, selection_revision=0, budget=budget,
    )
    service.ledger.consume_approval(approve(service.ledger, plan.task_id, plan.fingerprint))
    parameters_hash = digest({"prompt_version": "sam-prompt-v1", "prompts": [prompt]})
    binding = UIStepBinding(
        task_id="child", step_id="gpu", capability="ui.segment", inputs=plan.inputs,
        selection_ref=local_selection, selection_revision=0, selection_hash=local_selection.sha256,
        dependency_hashes={"sam-model": snapshot.sha256, "sam-parameters": parameters_hash},
    )
    operation = service.ledger.reserve(
        plan, "gpu", 0, Cost(gpu_minutes=1), resource="local-gpu", ui_binding=binding
    )
    assert service.ledger.begin_submit(operation.operation_id)
    job = SAMJob(
        task_id="child", operation_id=operation.operation_id, canonical_ref=canonical,
        selection_ref=local_selection, selection_revision=0, selection_hash=local_selection.sha256,
        model_snapshot_ref=snapshot, model_digest=snapshot.sha256, prompts=[prompt],
        parameters_hash=parameters_hash, resources=UIResourceLimits(),
    )
    permit = issue_sam_permit(service.ledger, service.artifacts, job, "s" * 32)
    assert permit

    mismatched_digest = job.model_copy(update={"model_digest": "f" * 64})
    with pytest.raises(PipelineError) as raised:
        issue_sam_permit(service.ledger, service.artifacts, mismatched_digest, "s" * 32)
    assert raised.value.code == "input_changed"

    expanded = job.model_copy(update={"resources": job.resources.model_copy(update={"vram_bytes": 1})})
    with pytest.raises(PipelineError) as raised:
        issue_sam_permit(service.ledger, service.artifacts, expanded, "s" * 32)
    assert raised.value.code == "input_changed"

    with service.ledger.transaction() as db:
        db.execute("DELETE FROM resources WHERE resource='local-gpu'")
    with pytest.raises(PipelineError) as raised:
        issue_sam_permit(service.ledger, service.artifacts, job, "s" * 32)
    assert raised.value.code == "resource_scope"


def test_approved_sam_child_uses_common_submit_recover_collect_and_release(tmp_path):
    budget = {
        "max_total_cost_usd": 0,
        "max_iteration_cost_usd": 0,
        "max_total_gpu_minutes": 2,
        "max_iteration_gpu_minutes": 2,
        "max_revisions": 0,
    }
    service = PipelineService(tmp_path / "coordinator", ui_schema=5)
    source = service.artifacts.put("root", "input", b"image", role="original", media_type="image/png")
    root = service.ui_plan(
        "root",
        UIAnalysisRequest(
            input={"kind": "manual", "inputs": [source]},
            output_mode="decompose",
            selection_mode="deferred",
            budget=budget,
        ),
    )
    selection_body = {
        "schema_version": 1,
        "sources": [
            {
                "source_id": source.artifact_id,
                "original_sha256": source.sha256,
                "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 10, 10]}],
                "keep_elements": [],
                "remove_elements": [],
            }
        ],
    }
    root_selection = service.artifacts.put(
        "root", "selection", canonical_json(selection_body).encode(), role="selection"
    )
    canonical = service.artifacts.put(
        "child", "input", b"image", role="canonical", media_type="image/png"
    )
    child_selection = service.artifacts.put(
        "child", "input", canonical_json(selection_body).encode(), role="selection"
    )
    snapshot = service.artifacts.put("child", "model", b"model", role="model_snapshot")
    prompt = {"element_id": "region-1", "box": [0, 0, 10, 10], "points": [[5.0, 5.0]]}
    request = {
        "schema_version": 1,
        "canonical_ref": canonical.model_dump(mode="json"),
        "selection_ref": child_selection.model_dump(mode="json"),
        "selection_revision": 0,
        "selection_hash": child_selection.sha256,
        "prompts": [prompt],
        "model_snapshot_ref": snapshot.model_dump(mode="json"),
        "prompt_version": "sam-prompt-v1",
        "resources": UIResourceLimits().model_dump(mode="json"),
        "result_roles": ["segmentation"],
    }
    request_ref = service.artifacts.put(
        "child", "request", canonical_json(request).encode(), role="request"
    )
    child = service.ui_child_plan(
        "child",
        parent_task_id=root.task_id,
        purpose="segmentation",
        source_ids=[source.artifact_id],
        request_ref=request_ref,
        selection_ref=root_selection,
        selection_revision=0,
        budget=budget,
    )
    service.ledger.consume_approval(approve(service.ledger, child.task_id, child.fingerprint))
    parameters_hash = digest({"prompt_version": "sam-prompt-v1", "prompts": [prompt]})
    binding = UIStepBinding(
        task_id=child.task_id,
        step_id="segment",
        capability="ui.segment",
        inputs=child.inputs,
        selection_ref=child_selection,
        selection_revision=0,
        selection_hash=child_selection.sha256,
        dependency_hashes={"sam-model": snapshot.sha256, "sam-parameters": parameters_hash},
    )

    class Engine:
        execution_ready = True
        calls = 0

        def segment(self, artifacts, job):
            self.calls += 1
            return {"schema_version": 1, "status": "ready", "operation_id": job.operation_id}

        def release(self):
            return True

    engine = Engine()
    application = VisionApplication(
        tmp_path / "provider",
        service.artifacts,
        token="local-auth",
        signing_key="s" * 32,
        engine=engine,
        model_digest=snapshot.sha256,
        capability="segmentation",
    )

    def handle(request):
        code, body = application.dispatch(request.method, request.url.path, request.headers, request.content)
        return httpx.Response(code, content=body if isinstance(body, bytes) else canonical_json(body).encode())

    client = VisionClient(
        token="local-auth",
        endpoint="http://127.0.0.1:8767",
        transport=httpx.MockTransport(handle),
    )
    backend = SAMOperationBackend(service.ledger, service.artifacts, client, signing_key="s" * 32)
    service.backends["ui.segment"] = backend
    operation = service.submit_step(child, binding, Cost(gpu_minutes=2))
    assert operation.state == "submitted"
    for _ in range(100):
        operation = service.observe_step(child, operation.operation_id)
        if operation.result.get("observation", {}).get("state") == "succeeded":
            break
        time.sleep(0.01)
    complete = service.collect_step(child, operation.operation_id)
    assert complete.state == "succeeded"
    assert [item["role"] for item in complete.result["artifacts"]] == ["segmentation"]
    assert service.submit_step(child, binding, Cost(gpu_minutes=2)).state == "succeeded"
    assert engine.calls == 1
    assert backend.release() == {"released": True, "device": "cuda"}


def test_sam_service_keeps_gpu_receipt_unknown_and_release_requires_ack(tmp_path, ui_store):
    class Engine:
        def release(self):
            return False

    app = VisionApplication(
        tmp_path, ui_store, token="local-auth", signing_key="x" * 32,
        engine=Engine(), model_digest="a" * 64, capability="segmentation"
    )
    headers = {"Authorization": "Bearer local-auth", "X-Task-ID": "sam-task"}
    code, body = app.dispatch("POST", "/v1/models/release", headers, b"")
    assert code == 409
    assert body["error_code"] == "resource_release_unknown"
    assert app.engine is not None

    class MeasuredEngine:
        release_metrics = {
            "initial_cuda_allocated_bytes": 128,
            "final_cuda_allocated_bytes": 64,
            "error_stage": None,
        }

        def release(self):
            return False

    app.engine = MeasuredEngine()
    code, body = app.dispatch("POST", "/v1/models/release", headers, b"")
    assert code == 409
    assert body == {
        "error_code": "resource_release_unknown",
        "release_metrics": MeasuredEngine.release_metrics,
    }
    assert app.engine is not None

    class BrokenEngine:
        def release(self):
            raise RuntimeError("provider details must not escape")

    app.engine = BrokenEngine()
    code, body = app.dispatch("POST", "/v1/models/release", headers, b"")
    assert code == 409
    assert body["error_code"] == "resource_release_unknown"
    assert app.engine is not None

    jobs = VisionJobs(tmp_path / "receipts")
    job = _sam_job(ui_store)
    receipt = jobs.accept(job)
    assert receipt["actual"] is None
    assert jobs.operation(job.operation_id, task_id=job.task_id)["actual"] is None


def test_sam_response_loss_recovery_is_a_single_read_only_operation_query():
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(404, json={"error_code": "not_found"})

    client = VisionClient(
        token="private-local-token",
        endpoint="http://127.0.0.1:8767",
        transport=httpx.MockTransport(handle),
    )
    assert client.operation("op-sam", task_id="sam-task") is None
    assert calls == [("GET", "/v1/operations/op-sam")]


def test_vision_recovery_rejects_a_response_with_wrong_payload_or_operation_identity(ui_store):
    from letsaigc.vision.client import OCROperationBackend

    view = ui_store.put("vision-task", "prepare", b"{}", role="view_manifest")

    class Ledger:
        def get(self, operation_id):
            return SimpleNamespace(operation_id=operation_id, task_id="vision-task")

        def ui_binding(self, operation_id):
            return SimpleNamespace(inputs=[view], parameters_ref=None)

    class Client:
        def operation(self, operation_id, *, task_id):
            return {
                "operation_id": "other-operation",
                "task_id": task_id,
                "payload_hash": "0" * 64,
                "request_id": "vision-request",
            }

    backend = OCROperationBackend(
        Ledger(), ui_store, Client(), signing_key="s" * 32, model_digest="a" * 64
    )
    with pytest.raises(PipelineError) as raised:
        backend.recover("op-vision")
    assert raised.value.code == "operation_scope"
