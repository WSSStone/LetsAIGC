"""T022 contracts for the isolated SAM2 service boundary.

These tests intentionally exercise only the model/runtime boundary.  Prompt
interpretation, mask construction and alpha/glyph extraction belong to T023.
"""

from __future__ import annotations

import hashlib
import json
import sys
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
from letsaigc.vision.client import VisionClient
from letsaigc.vision.sam_loader import (
    SAM2Engine,
    load_sam_settings,
    prepare_sam_view,
    validate_loaded_sam_preprocessing,
    validate_sam_model_files,
    validate_sam_preprocessing,
)
from letsaigc.vision.service import VisionApplication, VisionJobs

ROOT = Path(__file__).parents[2]


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
    assert evidence["status"] == "pending_verification"
    assert evidence["model"]["safetensors"]["sha256"] == model["files"]["model.safetensors"]["sha256"]
    assert evidence["model"]["processor_companion_hashes"]["status"] == "pending_verification"
    assert evidence["preprocessing"]["transformers_processor_source"].endswith(
        "/transformers/v4.57.6/src/transformers/models/sam2/processing_sam2.py"
    )


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
            assert device == "cuda"
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
            )
            self.target_size = 1024

        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            calls.append((args, kwargs))
            return cls()

    fake_torch = SimpleNamespace(cuda=SimpleNamespace(empty_cache=lambda: None))
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
            size={"height": 1024, "width": 1024}, default_to_square=True, do_pad=None
        ),
        target_size=1024,
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
