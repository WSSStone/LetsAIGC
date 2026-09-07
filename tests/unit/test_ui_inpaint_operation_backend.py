from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

import letsaigc.pipelines.ui_inpaint as ui_inpaint_module
from letsaigc.backends.comfy import ComfyBackend
from letsaigc.comfy.client import ComfyClient
from letsaigc.errors import RuntimeExecutionError
from letsaigc.pipelines.contracts import Submission
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.ui_inpaint import UIInpaintOperationBackend
from letsaigc.schemas.pipeline import ApprovalEnvelope, PipelinePlan
from letsaigc.schemas.ui import UIStepBinding


def _safety_fixture(tmp_path: Path, **kwargs):
    # Reuse the established synthetic v2 image/mask fixture.  It contains no
    # model weights and does not start a provider.
    path = Path(__file__).parents[1] / "contract" / "test_ui_inpaint_safety.py"
    spec = importlib.util.spec_from_file_location("ui_inpaint_safety_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module._fixture(tmp_path, **kwargs)


def _png(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _native_dependencies() -> dict[str, str]:
    root = Path(__file__).parents[2]
    paths = (
        "configs/workflows/recipes/sdxl-inpaint.yaml",
        "workflows/ui/sdxl-inpaint.json",
        "workflows/api/sdxl-inpaint.json",
        "workflows/contracts/sdxl-inpaint.yaml",
        "configs/models/catalog.yaml",
        "configs/runtime/comfyui.lock.yaml",
    )
    return {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths}


def _backend(tmp_path: Path, *, zero_mask: bool = False, canonical: Image.Image | None = None):
    mask = Image.new("L", (16, 12), 0) if zero_mask else None
    masked, artifacts, refs, *_ = _safety_fixture(tmp_path, canonical=canonical, canonical_mask=mask)
    request_ref = artifacts.put(
        masked.task_id,
        "request",
        masked.model_dump_json().encode(),
        role="request",
    )
    plan = PipelinePlan(
        task_id=masked.task_id,
        workflow_type="ui_inpaint",
        inputs=[request_ref, masked.image_mask.selection_ref],
        parameters={
            "request_ref": request_ref.model_dump(mode="json"),
            "selection_ref": masked.image_mask.selection_ref.model_dump(mode="json"),
            "selection_revision": masked.image_mask.selection_revision,
        },
        dependency_hashes=_native_dependencies(),
        envelope=ApprovalEnvelope(
            stage="generation",
            allowed_capabilities=["ui.inpaint"],
            budget=masked.envelope.budget,
        ),
    )
    binding = UIStepBinding(
        task_id=masked.task_id,
        step_id="gpu",
        capability="ui.inpaint",
        inputs=plan.inputs,
        selection_ref=masked.image_mask.selection_ref,
        selection_revision=masked.image_mask.selection_revision,
        selection_hash=masked.image_mask.selection_hash,
    )
    operation = SimpleNamespace(
        operation_id="op-inpaint",
        task_id=masked.task_id,
        provider_request_id=None,
        state="submitting",
        result={},
    )

    class Ledger:
        def get(self, _key):
            return operation

        def plan(self, _task_id):
            return plan

        def ui_binding(self, _key):
            return binding

        class _Transaction:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            class _Rows:
                @staticmethod
                def fetchall():
                    return [object()]

            @classmethod
            def execute(cls, *_args):
                return cls._Rows()

        def transaction(self):
            return self._Transaction()

    client = SimpleNamespace(
        base_url="http://127.0.0.1:8188",
        system_stats=lambda: {
            "system": {"comfyui_version": "0.34.2"},
            "devices": [{"type": "cuda", "index": 0, "torch_vram_total": 10, "torch_vram_free": 10}],
        },
        object_info=lambda: {
            node: {"python_module": "comfy"}
            for node in (
                "CheckpointLoaderSimple",
                "LoadImage",
                "ImageToMask",
                "VAEEncodeForInpaint",
                "CLIPTextEncode",
                "KSampler",
                "VAEDecode",
                "SaveImage",
            )
        },
    )
    native = ComfyBackend(client=client, artifact_store=artifacts)
    # This is the explicit test-only trust injection path.  Production uses
    # the backend's root-original/local-rebinding derivation.
    from letsaigc.ui_analysis.selection import TrustedSource

    native.trusted_sources = {
        "source-1": TrustedSource(
            "source-1",
            refs["original_ref"],
            width=16,
            height=12,
            layout_ref=refs["layout_ref"],
            canonical_ref=refs["canonical_ref"],
        )
    }
    return UIInpaintOperationBackend(ledger=Ledger(), artifacts=artifacts, backend=native), masked, artifacts, plan


def test_exact_prepared_pair_is_checked_before_provider_use(tmp_path: Path):
    backend, plan, artifacts, _child = _backend(tmp_path)
    wrong = artifacts.put(
        plan.task_id,
        "wrong",
        _png(Image.new("RGB", (2, 2), "red")),
        role="image",
        media_type="image/png",
    )
    changed = plan.model_copy(
        update={"image_mask": plan.image_mask.model_copy(update={"image_ref": wrong})}
    )
    with pytest.raises(Exception, match="dimensions"):
        backend._validate_image_mask(changed)


def test_cancel_does_not_issue_global_interrupt_with_other_queue_work(tmp_path: Path):
    backend, plan, artifacts, _child = _backend(tmp_path)
    operation = backend.ledger.get("ignored")
    receipt = {
        "run_id": "run-inpaint",
        "task_id": operation.task_id,
        "operation_id": operation.operation_id,
        "started_at": 1.0,
        "timeout_seconds": 600.0,
        "outputs": [],
        "graph_sha256": "a" * 64,
    }
    operation.provider_request_id = "prompt-owned"
    operation.result = {"receipt": receipt}

    calls: list[str] = []

    class Client:
        base_url = "http://127.0.0.1:8188"

        def history(self, _prompt):
            return {"prompt-owned": {"status": {"completed": False}}}

        def queue(self):
            return {"queue_running": [[0, "prompt-owned"]], "queue_pending": [[1, "other-prompt"]]}

        def cancel_owned_prompt(self, _prompt):
            calls.append("cancel")

    backend.backend.client = Client()
    with pytest.raises(PipelineError) as raised:
        backend.cancel(Submission(request_id="prompt-owned", metadata=receipt))
    assert raised.value.code == "resource_busy"
    assert calls == []


def test_preflight_reads_frozen_artifacts_with_read_only_native_probes(tmp_path: Path):
    backend, _plan, _artifacts, child = _backend(tmp_path)
    result = backend.preflight(child)
    assert result["ready"] is True
    assert result["prepared_size"] == [8, 8]
    assert result["canonical_size"] == [16, 12]


def test_approved_noop_submit_collect_release_uses_native_backend(tmp_path: Path):
    backend, _masked, _artifacts, _plan = _backend(tmp_path, zero_mask=True)
    binding = backend.ledger.ui_binding("op-inpaint")
    prepared = backend.prepare("op-inpaint", {"binding": binding.model_dump(mode="json")})
    submission = backend.submit(
        "op-inpaint",
        {"binding": binding.model_dump(mode="json"), "prepared": prepared},
    )
    operation = backend.ledger.get("op-inpaint")
    operation.provider_request_id = submission.request_id
    outputs = backend.collect(submission)
    release = backend.release_operation(submission)
    assert submission.request_id.startswith("noop-")
    assert [role for role, _data, _media_type in outputs] == ["reconstruction"]
    assert release["released"] is True
    assert release["noop"] is True


def test_submit_rejects_prepared_graph_override(tmp_path: Path):
    backend, _masked, _artifacts, _plan = _backend(tmp_path, zero_mask=True)
    binding = backend.ledger.ui_binding("op-inpaint")
    prepared = backend.prepare("op-inpaint", {"binding": binding.model_dump(mode="json")})
    prepared["graph"]["forged"] = {"class_type": "SaveImage", "inputs": {}}
    with pytest.raises(PipelineError, match="graph hash"):
        backend.submit(
            "op-inpaint",
            {"binding": binding.model_dump(mode="json"), "prepared": prepared},
        )


def test_reused_backend_rebuilds_trusted_sources_for_second_child(tmp_path: Path):
    backend, _masked, _artifacts, plan = _backend(tmp_path / "first")
    assert backend.preflight(plan)["ready"] is True
    second_backend, masked2, artifacts2, plan2 = _backend(
        tmp_path / "second", canonical=Image.new("RGB", (16, 12), (12, 34, 56))
    )
    refs2 = second_backend.backend.trusted_sources["source-1"]
    # Reuse the first coordinator/native backend with a different child store
    # and ledger.  The root-original resolver stands in for the approved root
    # plan that production supplies for this synthetic fixture.
    backend.ledger = second_backend.ledger
    backend.artifacts = artifacts2
    backend.backend.artifact_store = artifacts2
    backend._root_originals = lambda _plan: {refs2.original_sha256: refs2.original_ref}
    assert backend.preflight(plan2)["ready"] is True
    assert backend.backend.trusted_sources["source-1"].canonical_ref == masked2.image_mask.canonical_ref


def test_inpaint_readiness_sanitizes_probe_and_constructor_errors(monkeypatch):
    class FailingClient:
        def system_stats(self):
            raise RuntimeError("secret-provider-path")

        def object_info(self):
            raise RuntimeError("secret-object-token")

    result = ui_inpaint_module.inpaint_readiness(client=FailingClient())
    assert "comfy_probe_failed" in result["reasons"]
    assert all("secret-" not in reason for reason in result["reasons"])

    def fail_constructor(**_kwargs):
        raise RuntimeError("secret-constructor-path")

    monkeypatch.setattr(ui_inpaint_module, "ComfyClient", fail_constructor)
    result = ui_inpaint_module.inpaint_readiness()
    assert "comfy_client_unavailable" in result["reasons"]
    assert all("secret-" not in reason for reason in result["reasons"])


def test_inpaint_readiness_rejects_unpinned_version_even_with_all_resources(monkeypatch):
    from letsaigc.pipelines import ui_inpaint as module

    monkeypatch.setattr(module.ModelManager, "verify_model", lambda *args: [{"ok": True}])

    class Probe:
        def system_stats(self):
            return {"system": {"comfyui_version": "unapproved"}, "devices": [
                {"type": "cuda", "torch_vram_total": 1, "torch_vram_free": 1},
            ]}

        def object_info(self):
            return {name: {} for name in module.NATIVE_INPAINT_NODES}

    result = module.inpaint_readiness(client=Probe())
    assert result["model_files"] and result["cuda"] and result["object_info"]
    assert result["ready"] is False
    assert "comfy_version_unpinned" in result["reasons"]


def test_native_release_waits_for_empty_queue_and_zero_cuda_allocation(monkeypatch):
    client = ComfyClient("http://127.0.0.1:8188", timeout=0.2)
    queue = iter([
        {"queue_running": [], "queue_pending": []},
        {"queue_running": [], "queue_pending": []},
        {"queue_running": [], "queue_pending": []},
        {"queue_running": [], "queue_pending": []},
    ])
    stats = {
        "system": {"comfyui_version": "0.34.2"},
        "devices": [{"type": "cuda", "index": 0, "name": "test", "torch_vram_total": 10, "torch_vram_free": 10}],
    }
    stats_sequence = iter(
        [
            {
                "system": {"comfyui_version": "0.34.2"},
                "devices": [{"type": "cuda", "index": 0, "name": "test", "torch_vram_total": 10, "torch_vram_free": 5}],
            },
            stats,
        ]
    )
    posts: list[tuple[str, dict]] = []

    def fake_get(url, *, timeout):
        del timeout
        if url.endswith("/queue"):
            payload = next(queue)
        elif url.endswith("/system_stats"):
            payload = next(stats_sequence)
        else:
            raise AssertionError(url)
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    def fake_post(url, *, json, timeout):
        del timeout
        posts.append((url, json))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    result = client.release_models(timeout_seconds=0.2, poll_seconds=0)
    assert result["released"] is True
    assert result["devices"][0]["torch_vram_allocated"] == 0
    assert posts == [("http://127.0.0.1:8188/free", {"unload_models": True, "free_memory": True})]


def test_native_release_rejects_malformed_cuda_stats(monkeypatch):
    client = ComfyClient("http://127.0.0.1:8188", timeout=0.01)
    monkeypatch.setattr(
        client,
        "queue",
        lambda: {"queue_running": [], "queue_pending": []},
    )
    monkeypatch.setattr(
        client,
        "system_stats",
        lambda: {
            "system": {"comfyui_version": "0.34.2"},
            "devices": [{"type": "cuda", "torch_vram_total": 1.0, "torch_vram_free": 1.0}],
        },
    )
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(200, request=httpx.Request("POST", args[0])),
    )
    with pytest.raises(RuntimeExecutionError, match="counters are malformed"):
        client.release_models(timeout_seconds=0.01, poll_seconds=0)


def test_native_release_does_not_post_when_unrelated_queue_job_exists(monkeypatch):
    client = ComfyClient("http://127.0.0.1:8188")
    monkeypatch.setattr(
        client,
        "queue",
        lambda: {"queue_running": [], "queue_pending": [[1, "other-job"]]},
    )
    calls: list[str] = []
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: calls.append(args[0]) or httpx.Response(200, request=httpx.Request("POST", args[0])),
    )
    with pytest.raises(RuntimeExecutionError, match="queue jobs exist"):
        client.release_models(timeout_seconds=0.01, poll_seconds=0)
    assert calls == []
