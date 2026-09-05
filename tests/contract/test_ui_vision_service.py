import json

import httpx
import pytest

from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.pipeline import ArtifactRef, digest
from letsaigc.vision.base import OCRJob
from letsaigc.vision.client import VisionClient
from letsaigc.vision.service import VisionJobs, configure_paddlex_cache


def test_paddlex_cache_is_confined_to_runtime_root(tmp_path):
    environment = {}
    target = configure_paddlex_cache(tmp_path, ".local/cache/paddlex", environment=environment)
    assert target == (tmp_path / ".local/cache/paddlex").resolve()
    assert environment["PADDLE_PDX_CACHE_HOME"] == str(target)
    assert environment["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] == "True"
    with pytest.raises(PipelineError) as raised:
        configure_paddlex_cache(tmp_path, "../outside", environment={})
    assert raised.value.code == "cache_root"


def test_service_persists_operation_receipt_before_inference_and_restart_keeps_unknown(tmp_path, ui_store):
    ref = ui_store.put("vision-task", "prepare", b"{}", role="view_manifest")
    job = OCRJob(task_id="vision-task", operation_id="op-vision", view_ref=ref, model_digest="a" * 64)
    jobs = VisionJobs(tmp_path)
    first = jobs.accept(job)
    assert first["state"] == "accepted"
    assert first["request_id"] != job.operation_id
    assert first == jobs.accept(job)
    with pytest.raises(PipelineError):
        jobs.accept(job.model_copy(update={"language": "zh"}))
    restarted = VisionJobs(tmp_path)
    persisted = restarted.operation(job.operation_id, task_id=job.task_id)
    assert persisted["request_id"] == first["request_id"]
    assert persisted["state"] == "unknown"
    with pytest.raises(PipelineError):
        restarted.operation(job.operation_id, task_id="another-task")


def test_lost_receipt_recovery_uses_get_without_reposting_or_leaking_token():
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer private-local-token"
        return httpx.Response(404, json={"error_code": "not_found"})

    client = VisionClient(token="private-local-token", transport=httpx.MockTransport(handle))
    assert client.operation("op-lost", task_id="vision-task") is None
    assert calls == [("GET", "/v1/operations/op-lost")]
    with pytest.raises(PipelineError):
        VisionClient(token="private-local-token", endpoint="http://0.0.0.0:8766")


def test_authenticated_router_rejects_unsigned_and_wrong_scope_jobs(tmp_path, ui_store):
    from letsaigc.vision.service import VisionApplication

    app = VisionApplication(
        tmp_path, ui_store, token="local-auth", signing_key="x" * 32, engine=None, model_digest="a" * 64
    )
    ref = ui_store.put("vision-task", "input", b"{}", role="view_manifest")
    job = OCRJob(task_id="vision-task", operation_id="op-vision", view_ref=ref, model_digest="a" * 64)
    headers = {"Authorization": "Bearer local-auth", "X-Task-ID": "vision-task"}
    code, body = app.dispatch("GET", "/v1/health", {}, b"")
    assert code == 401
    code, body = app.dispatch("POST", "/v1/jobs", headers, job.model_dump_json().encode())
    assert code in {401, 403}
    assert app.jobs.operation("op-vision", task_id="vision-task") is None
    assert "local-auth" not in json.dumps(body)
    assert digest(job)  # The permit signs this exact bounded job payload.


def test_ocr_adapter_uses_public_admission_and_collects_the_original_receipt(tmp_path):
    import time

    from letsaigc.pipelines.approval import approve
    from letsaigc.pipelines.migrations import migrate_ui_ledger
    from letsaigc.pipelines.service import PipelineService
    from letsaigc.schemas.pipeline import Cost, canonical_json
    from letsaigc.schemas.ui import UIAnalysisRequest, UIStepBinding
    from letsaigc.vision.client import OCROperationBackend
    from letsaigc.vision.service import VisionApplication

    class Engine:
        def recognize(self, store, job):
            return {"schema_version": 1, "texts": [], "status": "empty"}

    service = PipelineService(tmp_path / "coordinator")
    migrate_ui_ledger(service.ledger.path, writers_stopped=True)
    original = service.artifacts.put("ocr-task", "input", b"fixture", role="original", media_type="image/png")
    model = service.artifacts.put("ocr-task", "input", canonical_json({"fixture": True}).encode(), role="model_policy")
    view = service.artifacts.put("ocr-task", "prepare", b"{}", role="view_manifest")
    request = UIAnalysisRequest(
        input={"kind": "manual", "inputs": [original]},
        model_bindings={"ocr": model},
        budget={
            "max_total_cost_usd": 0,
            "max_iteration_cost_usd": 0,
            "max_total_gpu_minutes": 0,
            "max_iteration_gpu_minutes": 0,
            "max_revisions": 2,
        },
    )
    plan = service.ui_plan("ocr-task", request)
    app = VisionApplication(
        tmp_path / "provider",
        service.artifacts,
        token="local-test",
        signing_key="s" * 32,
        engine=Engine(),
        model_digest=model.sha256,
    )
    methods = []

    def handle(request):
        methods.append(request.method)
        code, data = app.dispatch(request.method, request.url.path, request.headers, request.content)
        return httpx.Response(code, content=data) if isinstance(data, bytes) else httpx.Response(code, json=data)

    client = VisionClient(token="local-test", transport=httpx.MockTransport(handle))
    backend = OCROperationBackend(
        service.ledger, service.artifacts, client, signing_key="s" * 32, model_digest=model.sha256
    )
    service.backends["ui.ocr"] = backend
    service.ledger.consume_approval(approve(service.ledger, plan.task_id, plan.fingerprint))
    binding = UIStepBinding(
        task_id=plan.task_id,
        step_id="ocr",
        capability="ui.ocr",
        inputs=[view],
        dependency_hashes={"ocr-model": model.sha256},
    )
    operation = service.submit_step(plan, binding, Cost())
    for _ in range(100):
        observed = service.observe_step(plan, operation.operation_id)
        if observed.result["observation"]["state"] == "succeeded":
            break
        time.sleep(0.01)
    done = service.collect_step(plan, operation.operation_id)
    assert done.state == "succeeded"
    assert methods.count("POST") == 1
    assert (
        json.loads(service.artifacts.read(ArtifactRef.model_validate(done.result["artifacts"][0])))["status"] == "empty"
    )
