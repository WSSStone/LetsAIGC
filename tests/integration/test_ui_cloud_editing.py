"""Cloud product path, using SDK MockTransport only; no provider or GPU calls."""
import base64
import io
import json

import httpx
import pytest
from openai import OpenAI
from PIL import Image

from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.errors import OutcomeUnknown, PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.pipelines.ui_cloud import UICloudBackend
from letsaigc.schemas.agent import TaskBudget
from letsaigc.schemas.pipeline import ArtifactRef, Cost, canonical_json
from letsaigc.schemas.ui import UIAnalysisRequest
from letsaigc.ui_analysis.cloud_editing import freeze_policy
from letsaigc.ui_analysis.editing import EditingExecution
from letsaigc.ui_analysis.selection import record_selection


def png(size=(512, 512)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (31, 60, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def family(tmp_path, monkeypatch, request):
    monkeypatch.setattr("letsaigc.ui_analysis.cloud_editing.endpoint_fingerprint", lambda: "a" * 64)
    monkeypatch.setattr("letsaigc.pipelines.ui_cloud.endpoint_fingerprint", lambda: "a" * 64)
    monkeypatch.setattr("letsaigc.ui_analysis.cloud_editing.get_setting", lambda key, default: default)
    service = PipelineService(tmp_path, ui_schema=5)
    store = service.artifacts
    original = store.put("root", "input", png(), role="original", media_type="image/png")
    canonical = store.put("root", "input", png(), role="canonical", media_type="image/png")
    layout = store.put("root", "input", canonical_json({"schema_version": 2, "task_id": "root",
        "source_id": original.artifact_id, "width": 512, "height": 512, "texts": [],
        "elements": [{"element_id": "marker", "base_type": "image", "semantic_tags": ["map_marker"],
                      "bbox": [240, 240, 264, 264]}]}).encode(), role="review_layout")
    selection = store.put("root", "selection", canonical_json({"schema_version": 1, "sources": [{
        "source_id": original.artifact_id, "original_sha256": original.sha256,
        "layout_ref": layout.model_dump(mode="json"),
        "target_regions": [{"kind": "element", "element_id": "marker"}],
        "keep_elements": [], "remove_elements": ["marker"]}]}).encode(), role="selection")
    budget = TaskBudget(max_total_cost_usd=.11, max_iteration_cost_usd=.10,
                        max_total_gpu_minutes=0, max_iteration_gpu_minutes=0, max_revisions=0)
    policy = freeze_policy(store, "root", "Remove the blue marker; preserve the white dot.", budget,
                           reasoning=getattr(request, "param", "low"))
    root = service.ui_plan("root", UIAnalysisRequest(
        input={"kind": "manual", "inputs": [original]}, output_mode="reconstruct",
        reconstruction_target="map_surface", selection_mode="bound", selection_ref=selection,
        budget=budget, model_bindings={"canonical_ref": canonical, "layout_ref": layout, "cloud_inpaint": policy}))
    record_selection(store, "root", selection)
    return service, root


def client_for(handler):
    return OpenAI(api_key="fake-test-key", base_url="https://offline.invalid/v1", max_retries=0,
                  http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def guide_response(*, usage=True, status="completed"):
    value = {"type": "response.completed", "response": {"id": "resp-offline-guide", "status": status,
        "output": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "Remove blue marker. Preserve white dot and terrain."}]}],
        "usage": {"input_tokens": 100, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 0}}
        if usage else None}}
    return httpx.Response(200, headers={"content-type": "text/event-stream", "x-request-id": "req-guide"},
                          text="data: " + json.dumps(value) + "\n\ndata: [DONE]\n\n")


def image_response(*, usage=True):
    return httpx.Response(200, json={"created": 1, "data": [{"b64_json": base64.b64encode(png((1024, 1024))).decode()}],
        "usage": {"input_tokens_details": {"text_tokens": 100, "image_tokens": 256}, "output_tokens": 1756}
        if usage else None})


def install(service, stage, handler):
    backend = UICloudBackend(service, stage, client=client_for(handler))
    service.backends[backend.capability.id] = backend
    return backend


def submit(service, child):
    service.ledger.consume_approval(approve(service.ledger, child.task_id, child.fingerprint))
    return service.submit_step(child, EditingExecution(service).binding(child),
                               Cost(cost_usd=child.envelope.budget.max_iteration_cost_usd))


def finish(service, child, operation):
    service.observe_step(child, operation.operation_id)
    return service.collect_step(child, operation.operation_id)


def test_two_separate_approvals_whole_image_shared_cost_and_no_gpu(family):
    service, root = family
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["X-Client-Request-Id"]
        if request.url.path.endswith("/responses"):
            body = json.loads(request.content)
            assert body["reasoning"] == {"effort": "low"}
            assert body["stream"] is True
            return guide_response()
        assert request.url.path.endswith("/images/edits")
        assert b'name="mask"' not in request.content
        assert b"gpt-image-2" in request.content
        return image_response()

    install(service, "cloud_guide", handler)
    install(service, "cloud_inpaint", handler)
    editing = EditingExecution(service)
    guide = editing.prepare(root).child
    assert guide.parameters["purpose"] == "cloud_guide"
    assert editing.prepare(root).child == guide and not calls
    with pytest.raises(PipelineError):
        service.submit_step(guide, editing.binding(guide), Cost(cost_usd=.01))
    assert not calls
    assert finish(service, guide, submit(service, guide)).state == "succeeded"
    image = editing.prepare(root).child
    assert image.parameters["purpose"] == "cloud_inpaint"
    assert image.fingerprint != guide.fingerprint
    assert image.parameters["parent_task_id"] == root.task_id
    with pytest.raises(PipelineError):
        service.submit_step(image, editing.binding(image), Cost(cost_usd=.10))
    operation = finish(service, image, submit(service, image))
    assert operation.state == "succeeded"
    assert operation.result["receipt"]["request_id_source"] == "local_result"
    assert operation.result["receipt"]["submission_trace"]["provider_request_id"] is None
    ref = ArtifactRef.model_validate(operation.result["artifacts"][0])
    assert Image.open(io.BytesIO(service.artifacts.read(ref))).size == (1024, 1024)
    assert service.artifacts.read(ref) == png((1024, 1024))
    assert editing.prepare(root).state == "succeeded"
    assert len(calls) == 2
    usage = service.ledger.usage(root.task_id, include_children=True)
    assert 0 < sum(item.cost_usd for item in usage.values()) < .11
    assert all(item.gpu_minutes == 0 for item in usage.values())
    with service.ledger.transaction() as db:
        assert db.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
    service.submit_step(image, editing.binding(image), Cost(cost_usd=.10))
    assert len(calls) == 2


@pytest.mark.parametrize("completed", [False, True])
def test_inspect_uses_editing_ledger_not_old_failed_run(family, monkeypatch, completed):
    from typer.testing import CliRunner

    from letsaigc.cli import app
    from letsaigc.ui_analysis import cli

    service, root = family
    calls = []

    def handler(request):
        calls.append(request)
        return guide_response() if request.url.path.endswith("/responses") else image_response()

    install(service, "cloud_guide", handler)
    install(service, "cloud_inpaint", handler)
    editing = EditingExecution(service)
    child = editing.prepare(root).child
    if completed:
        finish(service, child, submit(service, child))
        child = editing.prepare(root).child
        finish(service, child, submit(service, child))
    old = {"state": "failed", "temporal_run_id": "old-run", "steps": [],
           "stop_reason": "activity_interrupted", "projection_sequence": 25}
    with service.ledger.transaction() as db:
        db.execute("INSERT INTO projections(task_id,sequence,payload) VALUES(?,?,?)",
                   (root.task_id, 25, canonical_json(old)))
    monkeypatch.setattr(cli, "service", lambda: service)
    before = len(calls)
    result = CliRunner().invoke(app, ["--json", "ui", "inspect", root.task_id, "--local"])
    assert result.exit_code == 0, result.stdout
    value = json.loads(result.stdout)
    assert value["status"] == ("succeeded" if completed else "awaiting_approval")
    assert value["status_source"] == "editing_ledger"
    assert value["task"] == old  # Historical evidence is not rewritten.
    assert value["source"] == "local_projection" and value["stale"] is True
    assert cli.local_projection(service, root.task_id) == old
    assert len(calls) == before


@pytest.mark.parametrize("failure", ["connect", "read", "write", "http"])
def test_unknown_error_is_safe_and_never_resubmitted(family, failure):
    service, root = family
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "http":
            return httpx.Response(502, headers={"x-request-id": "req-rejected", "set-cookie": "secret"},
                                  json={"error": {"message": "SENSITIVE provider body"}})
        cls = {"connect": httpx.ConnectTimeout, "read": httpx.ReadTimeout, "write": httpx.WriteTimeout}[failure]
        raise cls("SENSITIVE url?key=secret", request=request)

    install(service, "cloud_guide", handler)
    child = EditingExecution(service).prepare(root).child
    with pytest.raises(OutcomeUnknown):
        submit(service, child)
    op = service.ledger.list_operations(child.task_id)[0]
    assert op.state == "outcome_unknown"
    trace = op.result["submission_trace"]
    assert trace["attempt"] == 1
    assert trace["error_category"] == ("http_server_error" if failure == "http" else failure + "_timeout")
    assert "SENSITIVE" not in canonical_json(op.result) and "set-cookie" not in canonical_json(op.result)
    from letsaigc.ui_analysis.cli import _approval_view

    view = _approval_view(service, child)
    assert view["details"]["operations"][0]["trace"]["error_category"] == trace["error_category"]
    try:
        service.submit_step(child, EditingExecution(service).binding(child), Cost(cost_usd=.01))
    except OutcomeUnknown:
        pass
    assert len(calls) == 1


def test_durable_result_recovers_without_new_submission(family, monkeypatch):
    service, root = family
    calls = []
    backend = install(service, "cloud_guide", lambda req: calls.append(req) or guide_response())
    child = EditingExecution(service).prepare(root).child
    original = service.ledger.submitted
    monkeypatch.setattr(service.ledger, "submitted", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("interrupted")))
    with pytest.raises(OutcomeUnknown):
        submit(service, child)
    monkeypatch.setattr(service.ledger, "submitted", original)
    op = service.ledger.list_operations(child.task_id)[0]
    # New backend instance has no in-memory response; recover reads the persisted receipt only.
    service.backends["ui.cloud_guide"] = UICloudBackend(service, "cloud_guide", client=backend.client)
    assert service.backends["ui.cloud_guide"].recover(op.operation_id) is not None
    op = service.recover_step(child, op.operation_id)
    assert finish(service, child, op).state == "succeeded"
    assert len(calls) == 1


def test_image_without_usage_is_preserved_but_unsettled(family):
    service, root = family
    install(service, "cloud_guide", lambda _: guide_response())
    child = EditingExecution(service).prepare(root).child
    finish(service, child, submit(service, child))
    calls = []
    install(service, "cloud_inpaint", lambda req: calls.append(req) or image_response(usage=False))
    child = EditingExecution(service).prepare(root).child
    op = submit(service, child)
    with pytest.raises(OutcomeUnknown):
        finish(service, child, op)
    op = service.ledger.get(op.operation_id)
    assert op.state == "outcome_unknown"
    ref = ArtifactRef.model_validate(op.result["receipt"]["output_ref"])
    assert service.artifacts.read(ref) == png((1024, 1024))
    assert op.result["receipt"]["actual"] is None
    from letsaigc.ui_analysis.cli import _approval_view

    assert _approval_view(service, child)["details"]["operations"][0]["output_path"]
    assert EditingExecution(service).prepare(root).state == "awaiting_reconciliation"
    assert len(calls) == 1


def test_local_preparation_error_is_free_and_does_not_dispatch(family, monkeypatch):
    service, root = family
    calls = []
    install(service, "cloud_guide", lambda req: calls.append(req) or guide_response())
    child = EditingExecution(service).prepare(root).child
    monkeypatch.setattr("letsaigc.pipelines.ui_cloud.endpoint_fingerprint", lambda: "b" * 64)
    op = submit(service, child)
    assert op.state == "failed" and op.actual == Cost()
    assert op.result["error_code"] == "dependency_changed"
    assert calls == []


@pytest.mark.parametrize("status,usage,expected", [("incomplete", True, "failed"),
                                                  ("completed", False, "awaiting_reconciliation")])
def test_guide_failure_does_not_prepare_an_image_child(family, status, usage, expected):
    service, root = family
    install(service, "cloud_guide", lambda _: guide_response(status=status, usage=usage))
    child = EditingExecution(service).prepare(root).child
    op = submit(service, child)
    if usage:
        assert finish(service, child, op).state == "failed"
    else:
        with pytest.raises(OutcomeUnknown):
            finish(service, child, op)
    prepared = EditingExecution(service).prepare(root)
    assert prepared.state == expected and prepared.child.task_id == child.task_id


def test_temporal_activities_use_the_same_two_stage_chain(family, monkeypatch):
    from temporalio import activity

    from letsaigc.execution.temporal.ui_editing_activities import UIEditingActivities
    from letsaigc.execution.temporal.ui_messages import UIEditingActivityInput

    service, root = family
    monkeypatch.setattr(activity, "heartbeat", lambda *a: None)
    calls = []
    install(service, "cloud_guide", lambda req: calls.append(req) or guide_response())
    install(service, "cloud_inpaint", lambda req: calls.append(req) or image_response())
    activities = UIEditingActivities(service)
    argument = UIEditingActivityInput(task_id=root.task_id, plan_fingerprint=root.fingerprint)
    for stage in ("cloud_guide", "cloud_inpaint"):
        prepared = activities.edit_prepare(argument)
        child = prepared.child
        assert child.parameters["purpose"] == stage
        receipt = approve(service.ledger, child.task_id, child.fingerprint)
        approved = argument.model_copy(update={"child": child, "approval": receipt})
        assert activities.edit_approval_target(approved) == child
        op = activities.edit_submit(approved)
        observe = approved.model_copy(update={"operation_id": op.operation_id})
        activities.edit_observe(observe)
        assert activities.edit_collect(observe).state == "succeeded"
    assert activities.edit_prepare(argument).state == "succeeded"
    assert len(calls) == 2


def test_cli_inspect_exposes_actual_profile_and_preview_without_calling(family, monkeypatch):
    from typer.testing import CliRunner

    from letsaigc.cli import app

    service, root = family
    child = EditingExecution(service).prepare(root).child
    monkeypatch.setattr("letsaigc.ui_analysis.cli.service", lambda: service)
    result = CliRunner().invoke(app, ["--json", "ui", "inspect", root.task_id, "--local"])
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    pending = value["pending_approvals"][0]
    assert pending["task_id"] == child.task_id and len(pending["plan_fingerprint"]) == 64
    assert pending["details"]["policy"]["model"] == "gpt-image-2"
    assert pending["details"]["policy"]["reasoning"] == "low"
    assert pending["details"]["preview_path"]
    assert service.ledger.list_operations(child.task_id) == []


@pytest.mark.parametrize("family", ["high"], indirect=True)
def test_high_reasoning_is_frozen_and_sent_without_changing_budget(family):
    from letsaigc.schemas.ui_cloud import CloudEditPolicy

    service, root = family
    calls = []

    def handler(request):
        body = json.loads(request.content)
        assert body["reasoning"] == {"effort": "high"}
        assert body["max_output_tokens"] == 1024
        calls.append(request)
        return guide_response()

    backend = install(service, "cloud_guide", handler)
    child = EditingExecution(service).prepare(root).child
    profile = backend.request(child).policy
    assert profile.reasoning == "high"
    assert child.envelope.budget.max_iteration_cost_usd == .01
    low = CloudEditPolicy.model_validate(profile.model_dump(exclude={"reasoning"}))
    assert low.reasoning == "low"
    assert finish(service, child, submit(service, child)).state == "succeeded"
    assert len(calls) == 1


def test_explicit_image_retry_reuses_guide_and_preserves_unknown(family):
    from letsaigc.ui_analysis.cloud_editing import plan_image_retry

    service, root = family
    calls = []
    install(service, "cloud_guide", lambda req: calls.append(req) or guide_response())
    editing = EditingExecution(service)
    guide = editing.prepare(root).child
    finish(service, guide, submit(service, guide))
    install(service, "cloud_inpaint", lambda req: calls.append(req) or httpx.Response(503, json={}))
    image = editing.prepare(root).child
    with pytest.raises(OutcomeUnknown):
        submit(service, image)
    old = service.ledger.list_operations(image.task_id)[0]
    retry = plan_image_retry(service, image.task_id)
    assert plan_image_retry(service, image.task_id) == retry
    assert len(calls) == 2
    assert service.ledger.effective_budget(root.task_id).max_total_cost_usd == .11
    assert retry.parameters["root_task_id"] == root.task_id
    assert retry.fingerprint != image.fingerprint
    assert editing.prepare(root).child == retry
    with pytest.raises(PipelineError, match="fingerprint"):
        approve(service.ledger, retry.task_id, image.fingerprint)
    receipt = approve(service.ledger, retry.task_id, retry.fingerprint)
    assert service.ledger.effective_budget(root.task_id).max_total_cost_usd == .11
    service.ledger.consume_approval(receipt)
    service.ledger.consume_approval(receipt)
    assert service.ledger.effective_budget(root.task_id).max_total_cost_usd == .21
    install(service, "cloud_inpaint", lambda req: calls.append(req) or image_response())
    assert finish(service, retry, submit(service, retry)).state == "succeeded"
    assert service.ledger.effective_budget(root.task_id).max_total_cost_usd == .21
    assert service.ledger.plan(root.task_id) == root
    assert service.ledger.get(old.operation_id) == old
    assert len(calls) == 3
    assert editing.prepare(root).state == "succeeded"
    assert service.ledger.usage(root.task_id, include_children=True)["unsettled"].cost_usd == .1


@pytest.mark.parametrize("kind,expected", [
    ("empty", "invalid_image_count"), ("url", "image_download_unsafe_url"), ("missing", "missing_image_base64"),
    ("base64", "image_base64_decoding_failed"), ("dimensions", "invalid_image_dimensions"),
    ("format", "invalid_image_format"), ("pixels", "image_decoding_failed"),
])
def test_image_response_diagnostics_preserve_usage_without_leaking_body(family, kind, expected, monkeypatch):
    monkeypatch.setattr("letsaigc.pipelines.ui_cloud._ImageResultResolver._resolve_addresses",
                        staticmethod(lambda *_: ["127.0.0.1"]))
    service, root = family
    install(service, "cloud_guide", lambda _: guide_response())
    editing = EditingExecution(service)
    guide = editing.prepare(root).child
    finish(service, guide, submit(service, guide))
    data = [{}]
    if kind == "empty":
        data = []
    if kind == "url":
        data = [{"url": "https://private.invalid/image?token=SENSITIVE"}]
    if kind == "base64":
        data = [{"b64_json": "SENSITIVE invalid base64"}]
    if kind in {"dimensions", "format", "pixels"}:
        value = png((512, 512))
        if kind == "pixels":
            value = b"SENSITIVE-not-an-image"
        if kind == "format":
            out = io.BytesIO()
            Image.new("RGB", (1024, 1024)).save(out, format="JPEG")
            value = out.getvalue()
        data = [{"b64_json": base64.b64encode(value).decode()}]
    calls = []
    install(service, "cloud_inpaint", lambda req: calls.append(req) or httpx.Response(200, json={
        "data": data, "usage": {"input_tokens_details": {"text_tokens": 10, "image_tokens": 256},
                                 "output_tokens": 1756}}))
    child = editing.prepare(root).child
    with pytest.raises(OutcomeUnknown):
        submit(service, child)
    operation = service.ledger.list_operations(child.task_id)[0]
    trace = operation.result["submission_trace"]
    assert trace["error_category"] == expected
    assert trace["usage"]["image_output_tokens"] == 1756
    assert trace["attempt"] == 1 and len(calls) == 1
    assert "SENSITIVE" not in canonical_json(trace)
    assert trace.get("output_ref") is None
    if kind in {"dimensions", "format", "pixels"}:
        ref = ArtifactRef.model_validate(trace["candidate_ref"])
        assert service.artifacts.read(ref) == value
    if kind == "dimensions":
        assert trace["image_size"] == [512, 512] and trace["image_format"] == "PNG"


@pytest.mark.parametrize("role", ["cloud_image_candidate", "image", "cloud_receipt"])
def test_image_persistence_failure_is_distinct_and_usage_survives(family, monkeypatch, role):
    service, root = family
    install(service, "cloud_guide", lambda _: guide_response())
    editing = EditingExecution(service)
    guide = editing.prepare(root).child
    finish(service, guide, submit(service, guide))
    child = editing.prepare(root).child
    install(service, "cloud_inpaint", lambda _: image_response())
    original = service.artifacts.put

    def put(*args, **kwargs):
        if kwargs.get("role") == role and args[1].startswith("op-"):
            raise OSError("SENSITIVE local path")
        return original(*args, **kwargs)

    monkeypatch.setattr(service.artifacts, "put", put)
    with pytest.raises(OutcomeUnknown):
        submit(service, child)
    trace = service.ledger.list_operations(child.task_id)[0].result["submission_trace"]
    stage = {"cloud_image_candidate": "candidate", "image": "output", "cloud_receipt": "receipt"}[role]
    assert trace["error_category"] == stage + "_persisting_failed"
    assert trace["usage"]["image_output_tokens"] == 1756
    assert "SENSITIVE" not in canonical_json(trace)
    if role in {"image", "cloud_receipt"}:
        from letsaigc.ui_analysis.cloud_editing import plan_image_retry

        with pytest.raises(PipelineError) as saved:
            plan_image_retry(service, child.task_id)
        assert saved.value.code == "cloud_result_available"
    if role == "cloud_receipt":
        assert service.artifacts.read(ArtifactRef.model_validate(trace["output_ref"])) == png((1024, 1024))


def failed_image_family(family):
    service, root = family
    install(service, "cloud_guide", lambda _: guide_response())
    editing = EditingExecution(service)
    guide = editing.prepare(root).child
    finish(service, guide, submit(service, guide))
    image = editing.prepare(root).child
    install(service, "cloud_inpaint", lambda _: httpx.Response(503, json={}))
    with pytest.raises(OutcomeUnknown):
        submit(service, image)
    return service, root, image


def test_retry_cli_is_offline_and_idempotent(family, monkeypatch):
    from typer.testing import CliRunner

    from letsaigc.cli import app

    service, root, image = failed_image_family(family)
    monkeypatch.setattr("letsaigc.ui_analysis.cli.service", lambda: service)
    args = ["--json", "ui", "retry-image", image.task_id]
    first = CliRunner().invoke(app, args)
    assert first.exit_code == 0, first.output
    second = CliRunner().invoke(app, args)
    assert second.exit_code == 0, second.output
    a, b = json.loads(first.output), json.loads(second.output)
    assert a["plan_fingerprint"] == b["plan_fingerprint"]
    assert a["retry"]["budget_after"]["max_total_cost_usd"] == .21
    assert a["details"]["retry"] == a["retry"]
    assert a["model_calls"] == 0 and a["gpu_calls"] == 0
    assert service.ledger.list_operations(a["task_id"]) == []
    assert service.ledger.effective_budget(root.task_id).max_total_cost_usd == .11


@pytest.mark.parametrize("change", ["unfinished", "running", "resource", "budget", "cross_root"])
def test_retry_rejects_unsafe_or_mismatched_bindings(family, change):
    from letsaigc.pipelines.cloud_retry import validate_retry
    from letsaigc.ui_analysis.cloud_editing import plan_image_retry

    service, root, image = failed_image_family(family)
    retry = plan_image_retry(service, image.task_id)
    operation = service.ledger.list_operations(image.task_id)[0]
    with pytest.raises(PipelineError), service.ledger.transaction() as db:
        if change == "unfinished":
            result = operation.result.copy()
            result["submission_trace"] = {"stage": "cloud_inpaint"}
            db.execute("UPDATE operations SET result=? WHERE operation_id=?",
                       (canonical_json(result), operation.operation_id))
        elif change == "running":
            db.execute("UPDATE operations SET state='running' WHERE operation_id=?", (operation.operation_id,))
        elif change == "resource":
            db.execute("INSERT INTO resources VALUES('gpu',?,1)", (operation.operation_id,))
        else:
            params = json.loads(canonical_json(retry.parameters))
            if change == "budget":
                params["cloud_retry"]["budget_after"]["max_total_cost_usd"] = 9
            else:
                params["root_task_id"] = "unrelated-root"
            retry = retry.model_copy(update={"parameters": params})
        validate_retry(db, retry)
    assert service.ledger.get(operation.operation_id) == operation
    assert service.ledger.effective_budget(root.task_id).max_total_cost_usd == .11


def test_retry_temporal_activities_preserve_exact_target_and_budget(family, monkeypatch):
    from temporalio import activity

    from letsaigc.execution.temporal.ui_editing_activities import UIEditingActivities
    from letsaigc.execution.temporal.ui_messages import UIEditingActivityInput
    from letsaigc.ui_analysis.cloud_editing import plan_image_retry

    service, root, image = failed_image_family(family)
    retry = plan_image_retry(service, image.task_id)
    monkeypatch.setattr(activity, "heartbeat", lambda *a: None)
    install(service, "cloud_inpaint", lambda _: image_response())
    activities = UIEditingActivities(service)
    arg = UIEditingActivityInput(task_id=root.task_id, plan_fingerprint=root.fingerprint,
        child=image, approval=approve(service.ledger, retry.task_id, retry.fingerprint))
    assert activities.edit_approval_target(arg) == retry
    arg = arg.model_copy(update={"child": retry})
    op = activities.edit_submit(arg)
    arg = arg.model_copy(update={"operation_id": op.operation_id})
    activities.edit_observe(arg)
    assert activities.edit_collect(arg).state == "succeeded"
    # Restored activity does not submit again after provider completion.
    assert activities.edit_submit(arg).operation_id == op.operation_id


def test_image_invalid_json_has_parse_classification(family):
    service, root = family
    install(service, "cloud_guide", lambda _: guide_response())
    editing = EditingExecution(service)
    guide = editing.prepare(root).child
    finish(service, guide, submit(service, guide))
    child = editing.prepare(root).child
    install(service, "cloud_inpaint", lambda _: httpx.Response(
        200, headers={"content-type": "application/json"}, text="SENSITIVE not JSON"))
    with pytest.raises(OutcomeUnknown):
        submit(service, child)
    trace = service.ledger.list_operations(child.task_id)[0].result["submission_trace"]
    assert trace["error_category"] == "response_body_parsing_failed"
    assert "SENSITIVE" not in canonical_json(trace)


@pytest.mark.parametrize("mode", ["success", "missing_usage", "over_budget", "bad_pixels", "expired"])
def test_url_image_product_collection_and_no_resubmit(family, monkeypatch, mode):
    service, root = family
    install(service, "cloud_guide", lambda _: guide_response())
    editing = EditingExecution(service)
    guide = editing.prepare(root).child
    finish(service, guide, submit(service, guide))
    child = editing.prepare(root).child
    calls, downloads = [], []
    response = json.loads(image_response(usage=mode != "missing_usage").content)
    response["data"] = [{"url": "https://cdn.test/SENSITIVE-path?token=SENSITIVE-query"}]
    if mode == "over_budget":
        response["usage"]["output_tokens"] = 100000
    backend = install(service, "cloud_inpaint", lambda req: calls.append(req) or httpx.Response(200, json=response))
    monkeypatch.setattr("letsaigc.pipelines.ui_cloud._ImageResultResolver._resolve_addresses",
                        staticmethod(lambda *_: ["93.184.216.34"]))

    def download(req):
        downloads.append(req)
        assert "authorization" not in req.headers
        return httpx.Response(403 if mode == "expired" else 200,
                              content=b"invalid" if mode == "bad_pixels" else png((1024, 1024)))

    monkeypatch.setattr("letsaigc.pipelines.ui_cloud.httpx.HTTPTransport",
                        lambda **_: httpx.MockTransport(download))
    if mode in {"bad_pixels", "expired"}:
        with pytest.raises(OutcomeUnknown):
            submit(service, child)
        operation = service.ledger.list_operations(child.task_id)[0]
        trace = operation.result["submission_trace"]
        assert trace["error_category"] == (
            "image_download_http_error" if mode == "expired" else "image_decoding_failed")
        assert backend.recover(operation.operation_id) is None
    else:
        operation = submit(service, child)
        if mode == "missing_usage":
            with pytest.raises(OutcomeUnknown):
                finish(service, child, operation)
        else:
            assert finish(service, child, operation).state == ("failed" if mode == "over_budget" else "succeeded")
        recovered = backend.recover(operation.operation_id)
        assert recovered is not None
        assert backend.collect(recovered) == [("image", png((1024, 1024)), "image/png")]
        trace = recovered.metadata["submission_trace"]
    assert trace["image_transport"] == "url" and trace["attempt"] == 1
    assert trace["download"]["attempt"] == 1
    assert "SENSITIVE" not in canonical_json(trace)
    assert trace["http_status"] == 200  # Download status must not replace generation status.
    if mode != "missing_usage":
        assert trace["usage"]["image_output_tokens"] > 0
    if mode in {"bad_pixels", "expired"}:
        with pytest.raises(OutcomeUnknown):
            service.submit_step(child, editing.binding(child), Cost(cost_usd=.1))
    else:
        # A durable receipt (even without usage) recovers locally, not by GET
        # or generation. It must retain the same operation and budget verdict.
        restored = service.submit_step(child, editing.binding(child), Cost(cost_usd=.1))
        assert restored.operation_id == operation.operation_id
    assert len(calls) == len(downloads) == 1


def test_base64_result_does_not_fetch_also_present_url(family, monkeypatch):
    service, root = family
    install(service, "cloud_guide", lambda _: guide_response())
    editing = EditingExecution(service)
    guide = editing.prepare(root).child
    finish(service, guide, submit(service, guide))
    response = json.loads(image_response().content)
    response["data"][0]["url"] = "http://127.0.0.1/do-not-fetch"
    install(service, "cloud_inpaint", lambda _: httpx.Response(200, json=response))
    monkeypatch.setattr("letsaigc.pipelines.ui_cloud.download_image_url", lambda *_: pytest.fail("downloaded"))
    child = editing.prepare(root).child
    assert finish(service, child, submit(service, child)).state == "succeeded"


def failed_first_retry(family):
    from letsaigc.ui_analysis.cloud_editing import plan_image_retry

    service, root, original = failed_image_family(family)
    first = plan_image_retry(service, original.task_id)
    with pytest.raises(OutcomeUnknown):
        submit(service, first)
    return service, root, original, first


def test_followup_retry_budget_ancestry_and_temporal_target(family, monkeypatch):
    from temporalio import activity

    from letsaigc.execution.temporal.ui_editing_activities import UIEditingActivities
    from letsaigc.execution.temporal.ui_messages import UIEditingActivityInput
    from letsaigc.ui_analysis.cloud_editing import plan_image_retry

    service, root, original, first = failed_first_retry(family)
    old = [service.ledger.list_operations(p.task_id)[0] for p in (original, first)]
    second = plan_image_retry(service, first.task_id, image_budget_usd=.15)
    assert plan_image_retry(service, first.task_id, image_budget_usd=.15) == second
    assert EditingExecution(service).prepare(root).child == second
    binding = second.parameters["cloud_retry"]
    assert binding["base_task_id"] == first.task_id
    assert binding["budget_before"]["max_total_cost_usd"] == .21
    assert binding["budget_after"]["max_total_cost_usd"] == .36
    assert binding["budget_after"]["max_iteration_cost_usd"] == .15
    assert binding["budget_after"]["max_revisions"] == 2
    assert service.ledger.effective_budget(root.task_id).max_total_cost_usd == .21
    calls = []
    backend = install(service, "cloud_inpaint", lambda req: calls.append(req) or image_response())
    after = backend.request(second)
    before = backend.request(first)
    assert after.prompt == before.prompt and after.context == before.context
    assert after.image_ref.sha256 == before.image_ref.sha256
    assert after.guidance_ref.sha256 == before.guidance_ref.sha256
    assert after.policy.image_budget_usd == .15
    with pytest.raises(PipelineError):
        service.submit_step(second, EditingExecution(service).binding(second), Cost(cost_usd=.15))
    assert calls == [] and service.ledger.list_operations(second.task_id) == []
    monkeypatch.setattr(activity, "heartbeat", lambda *_: None)
    activities = UIEditingActivities(service)
    receipt = approve(service.ledger, second.task_id, second.fingerprint)
    argument = UIEditingActivityInput(task_id=root.task_id, plan_fingerprint=root.fingerprint,
                                     child=first, approval=receipt)
    assert activities.edit_approval_target(argument) == second
    service.ledger.consume_approval(receipt)  # Duplicate delivery cannot grant again.
    assert service.ledger.effective_budget(root.task_id).max_total_cost_usd == .36
    argument = argument.model_copy(update={"child": second})
    operation = activities.edit_submit(argument)
    argument = argument.model_copy(update={"operation_id": operation.operation_id})
    activities.edit_observe(argument)
    assert activities.edit_collect(argument).state == "succeeded"
    assert activities.edit_submit(argument).operation_id == operation.operation_id
    assert len(calls) == 1
    assert service.ledger.plan(root.task_id) == root
    for previous in old:
        assert service.ledger.get(previous.operation_id) == previous
    assert service.ledger.usage(root.task_id, include_children=True)["unsettled"].cost_usd == .2
    assert EditingExecution(service).prepare(root).state == "succeeded"


def test_followup_retry_cli_requires_explicit_budget_and_stops_at_two(family, monkeypatch):
    from typer.testing import CliRunner

    from letsaigc.cli import app
    from letsaigc.ui_analysis.cloud_editing import plan_image_retry

    service, root, _, first = failed_first_retry(family)
    with pytest.raises(PipelineError, match="budget"):
        plan_image_retry(service, first.task_id)
    monkeypatch.setattr("letsaigc.ui_analysis.cli.service", lambda: service)
    args = ["--json", "ui", "retry-image", first.task_id, "--image-budget", "0.15"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    second = service.ledger.plan(value["task_id"])
    assert value["model_calls"] == 0
    assert value["retry"]["budget_after"]["max_total_cost_usd"] == .36
    assert service.ledger.list_operations(second.task_id) == []
    with pytest.raises(PipelineError, match="budget"):
        plan_image_retry(service, first.task_id, image_budget_usd=.2)
    with pytest.raises(OutcomeUnknown):
        submit(service, second)
    with pytest.raises(PipelineError) as capped:
        plan_image_retry(service, second.task_id, image_budget_usd=.15)
    assert capped.value.code == "cloud_retry_conflict"
    assert service.ledger.effective_budget(root.task_id).max_total_cost_usd == .36


@pytest.mark.parametrize("change", ["running", "resource", "unconsumed", "budget", "fork", "cycle"])
def test_followup_retry_rejects_invalid_chain(family, change):
    from letsaigc.pipelines.cloud_retry import validate_retry
    from letsaigc.ui_analysis.cloud_editing import plan_image_retry

    service, root, original, first = failed_first_retry(family)
    second = plan_image_retry(service, first.task_id, image_budget_usd=.15)
    operation = service.ledger.list_operations(original.task_id)[0]
    with pytest.raises(PipelineError), service.ledger.transaction() as db:
        if change == "running":
            db.execute("UPDATE operations SET state='running' WHERE operation_id=?", (operation.operation_id,))
        elif change == "resource":
            db.execute("INSERT INTO resources VALUES('gpu',?,1)", (operation.operation_id,))
        elif change == "unconsumed":
            db.execute("UPDATE approvals SET consumed=0 WHERE task_id=?", (first.task_id,))
        else:
            params = json.loads(canonical_json(second.parameters))
            if change == "budget":
                params["cloud_retry"]["budget_before"]["max_total_cost_usd"] = .11
            elif change == "fork":
                params["cloud_retry"]["base_task_id"] = original.task_id
            else:
                params["cloud_retry"]["base_task_id"] = second.task_id
            second = second.model_copy(update={"parameters": params})
        validate_retry(db, second)
    assert service.ledger.get(operation.operation_id) == operation
    assert service.ledger.effective_budget(root.task_id).max_total_cost_usd == .21
