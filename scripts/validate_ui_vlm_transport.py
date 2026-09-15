"""Prepare and run one explicitly approved T030 minimal VLM transport probe.

The plan command is offline.  The execute command accepts only its exact
fingerprint and permanently records dispatch before making one provider call.
It does not read or mutate the product pipeline ledger.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from letsaigc.agent.responses import (
    DEFAULT_VLM_MODEL,
    ResponsesAgentModel,
    build_llm_client,
    endpoint_fingerprint,
)
from letsaigc.agent.ui_analyzer import CORRELATION_STRATEGY as UI_CORRELATION_STRATEGY
from letsaigc.agent.ui_analyzer import (
    EVIDENCE_POLICY,
    PROVIDER_IMAGE_ENCODING,
    STREAMING_RESPONSE_TRANSPORT,
    UIAnalysisBackend,
    UIAnalyzer,
    VLMSubmissionError,
)
from letsaigc.config import get_setting
from letsaigc.execution.temporal.config import runtime_root
from letsaigc.generation.pricing import calculate_luna_cost, ensure_current, load_pricing
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import canonical_json, digest

ROOT = Path(__file__).resolve().parents[1]
VALIDATION_PARENT = (ROOT / ".local/validation/ui-analysis/t030-windows").resolve()
DEFAULT_VALIDATION_ROOT = "vlm-diagnostics/call-1-minimal"
PRODUCTION_VALIDATION_ROOT = "vlm-diagnostics/call-2-production-low"
STREAMING_PRODUCTION_VALIDATION_ROOT = "vlm-diagnostics/call-3-production-low-streaming"
CORRELATION_STRATEGY = "operation-id-header-v1"
PROMPT = "Describe the solid-color test image in one short sentence."
# A deterministic 1x1 opaque PNG; no user or commercial source data.
IMAGE_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
PROFILE = {
    "reasoning_effort": "low",
    "image_detail": "low",
    "provider_image_encoding": "embedded-png-fixture-v1",
    "schema_profile": "none",
    "correlation_strategy": CORRELATION_STRATEGY,
    "connect_timeout_seconds": 10,
    "write_timeout_seconds": 30,
    "pool_timeout_seconds": 10,
    "request_timeout_seconds": 60,
    "max_output_tokens": 64,
}
BUDGET = {
    "call_number": 1,
    "call_limit": 4,
    "max_call_cost_usd": 0.005,
    "max_aggregate_cost_usd": 0.10,
}
PRODUCTION_SOURCE_TASK_ID = "ui-9ba659803e574bda833ec037716f02ac"
PRODUCTION_SOURCE_OPERATION_ID = "op-74a00c1ceea62f413405ad4dfb3893bf1532c465096dc60c"
PRODUCTION_BUDGET = {
    "call_number": 2,
    "call_limit": 4,
    "max_call_cost_usd": 0.025,
    "max_aggregate_cost_usd": 0.10,
    "prior_local_actual_cost_usd": 0.0001994,
}
STREAMING_PRODUCTION_BUDGET = {
    "call_number": 3,
    "call_limit": 4,
    "max_call_cost_usd": 0.03,
    "max_aggregate_cost_usd": 0.10,
    "prior_local_actual_cost_usd": 0.0001994,
    "prior_unknown_reserved_cost_usd": 0.025,
    "max_aggregate_if_fully_charged_usd": 0.0551994,
}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _root(value: str) -> Path:
    path = (VALIDATION_PARENT / value).resolve()
    if not path.is_relative_to(VALIDATION_PARENT):
        raise PipelineError("invalid_path", "VLM diagnostic evidence must stay under the T030 validation root")
    return path


def _request(model: str) -> dict:
    image = base64.b64encode(IMAGE_BYTES).decode("ascii")
    return {
        "model": model,
        "reasoning": {"effort": PROFILE["reasoning_effort"]},
        "store": False,
        "max_output_tokens": PROFILE["max_output_tokens"],
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": PROMPT},
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64," + image,
                        "detail": PROFILE["image_detail"],
                    },
                ],
            }
        ],
    }


def _material() -> tuple[dict, dict]:
    model = get_setting("LLM_VLM_MODEL", DEFAULT_VLM_MODEL)
    pricing = load_pricing()
    ensure_current(pricing)
    calculate_luna_cost(pricing, {}, model=model)
    request = _request(model)
    request_bytes = canonical_json(request).encode()
    request_sha256 = hashlib.sha256(request_bytes).hexdigest()
    diagnostic_id = "t030-vlm-call-1-" + request_sha256[:20]
    material = {
        "schema_version": 1,
        "kind": "minimal_vlm_baseline",
        "diagnostic_id": diagnostic_id,
        "model": model,
        "endpoint_fingerprint": endpoint_fingerprint(),
        "request_profile": PROFILE,
        "request_sha256": request_sha256,
        "request_size_bytes": len(request_bytes),
        "image_sha256": hashlib.sha256(IMAGE_BYTES).hexdigest(),
        "image_size": [1, 1],
        "budget": BUDGET,
    }
    return material, request


def plan(value: str = DEFAULT_VALIDATION_ROOT) -> dict:
    root = _root(value)
    material, _ = _material()
    result = {**material, "plan_fingerprint": digest(material), "provider_calls": 0}
    root.mkdir(parents=True, exist_ok=True)
    (root / "plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _exception_category(exc: Exception) -> str:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        name = type(exc).__name__.lower()
        for marker, category in (
            ("connecttimeout", "connect_timeout"),
            ("writetimeout", "write_timeout"),
            ("readtimeout", "read_timeout"),
            ("pooltimeout", "pool_timeout"),
        ):
            if marker in name:
                return category
        if getattr(exc, "status_code", None) is not None:
            return "http_rejection"
        exc = exc.__cause__ or exc.__context__
    return "sdk_dispatch_error"


def _response_evidence(value) -> tuple[int | None, str | None]:
    response = getattr(value, "response", None)
    status = getattr(value, "status_code", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    headers = getattr(value, "headers", None)
    if headers is None and response is not None:
        headers = getattr(response, "headers", None)
    return status, headers.get("x-request-id") if headers is not None else None


def _write_result(root: Path, result: dict) -> dict:
    (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def execute(value: str, fingerprint: str, *, client=None) -> dict:
    root = _root(value)
    plan_path = root / "plan.json"
    if not plan_path.is_file():
        raise PipelineError("plan_missing", "Run the offline VLM diagnostic plan first")
    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    material, request = _material()
    expected = digest(material)
    if fingerprint != expected or saved != {**material, "plan_fingerprint": expected, "provider_calls": 0}:
        raise PipelineError("plan_changed", "VLM diagnostic approval does not match the current exact request")
    attempt_path = root / "attempt.json"
    if attempt_path.exists():
        raise PipelineError("call_limit", "This diagnostic call already crossed its dispatch boundary")

    timeout = httpx.Timeout(
        connect=PROFILE["connect_timeout_seconds"],
        write=PROFILE["write_timeout_seconds"],
        pool=PROFILE["pool_timeout_seconds"],
        read=PROFILE["request_timeout_seconds"],
    )
    owned_client = client is None
    if owned_client:
        # Local credentials and SDK construction are checked before the
        # durable dispatch marker; neither action contacts the provider.
        client = build_llm_client(timeout=timeout)

    trace = {
        "schema_version": 1,
        "diagnostic_id": material["diagnostic_id"],
        "client_request_id": material["diagnostic_id"],
        "attempt": 1,
        "request_profile": material["request_profile"],
        "request_sha256": material["request_sha256"],
        "request_size_bytes": material["request_size_bytes"],
        "started_at": _timestamp(),
        "finished_at": None,
        "elapsed_ms": None,
        "stages": [],
        "http_status": None,
        "provider_request_id": None,
        "provider_response_id": None,
        "usage": None,
        "actual_cost_usd": None,
        "error_category": None,
    }
    attempt_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "diagnostic_id": material["diagnostic_id"],
                "plan_fingerprint": expected,
                "dispatch_started_at": trace["started_at"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    started = time.monotonic()
    try:
        trace["stages"].append({"name": "sdk_dispatch_started", "at": _timestamp()})
        try:
            raw = client.responses.with_raw_response.create(
                **request,
                extra_headers={"X-Client-Request-Id": material["diagnostic_id"]},
                timeout=timeout,
            )
        except Exception as exc:
            trace["http_status"], trace["provider_request_id"] = _response_evidence(exc)
            if trace["http_status"] is not None:
                trace["stages"].append({"name": "response_headers_received", "at": _timestamp()})
            trace["error_category"] = _exception_category(exc)
            return _write_result(root, {"state": "outcome_unknown", "trace": trace})
        trace["http_status"] = getattr(raw, "status_code", None)
        headers = getattr(raw, "headers", None)
        trace["provider_request_id"] = headers.get("x-request-id") if headers is not None else None
        trace["stages"].append({"name": "response_headers_received", "at": _timestamp()})
        try:
            response = raw.parse()
        except Exception:
            trace["error_category"] = "response_parse_error"
            return _write_result(root, {"state": "outcome_unknown", "trace": trace})
        trace["stages"].append({"name": "response_parsed", "at": _timestamp()})
        trace["provider_response_id"] = getattr(response, "id", None)
        try:
            ResponsesAgentModel._check_response(response)
            output = getattr(response, "output_text", "")
            if not output:
                raise ValueError
            usage = ResponsesAgentModel._usage_dict(getattr(response, "usage", None))
            pricing = load_pricing()
            ensure_current(pricing)
            actual = calculate_luna_cost(pricing, usage, model=material["model"])
            trace["usage"] = usage
            trace["actual_cost_usd"] = actual
            if actual > BUDGET["max_call_cost_usd"]:
                trace["error_category"] = "budget_exceeded"
                state = "failed"
            else:
                state = "succeeded"
            return _write_result(
                root,
                {
                    "state": state,
                    "trace": trace,
                    "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                    "output_size_bytes": len(output.encode()),
                },
            )
        except Exception:
            trace["error_category"] = "response_validation_error"
            return _write_result(root, {"state": "outcome_unknown", "trace": trace})
    finally:
        trace["finished_at"] = _timestamp()
        trace["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        # Rewrite the result after final timing fields are populated.
        result_path = root / "result.json"
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["trace"] = trace
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if owned_client:
            client.close()


def _production_context(*, response_transport=None):
    service = PipelineService(runtime_root(), ui_schema=3)
    operation = service.ledger.get(PRODUCTION_SOURCE_OPERATION_ID)
    if (
        operation.task_id != PRODUCTION_SOURCE_TASK_ID
        or operation.step_id != "analyze"
        or operation.state != "outcome_unknown"
        or operation.provider_request_id is not None
    ):
        raise PipelineError("source_changed", "The historical unknown operation is not in its preserved state")
    binding = service.ledger.ui_binding(PRODUCTION_SOURCE_OPERATION_ID)
    historical, view, ocr, notes, _, _, source_plan = UIAnalysisBackend(
        service.ledger, service.artifacts
    )._prepared({"binding": binding.model_dump(mode="json")})
    analyzer = UIAnalyzer(
        model=historical.model,
        pricing=historical.pricing,
        max_output_tokens=4096,
        evidence_policy=EVIDENCE_POLICY,
        reasoning_effort="low",
        image_detail="high",
        provider_image_encoding=PROVIDER_IMAGE_ENCODING,
        correlation_strategy=UI_CORRELATION_STRATEGY,
        response_transport=response_transport,
        connect_timeout_seconds=10,
        write_timeout_seconds=30,
        pool_timeout_seconds=10,
        request_timeout_seconds=300,
    )
    prepared = analyzer.prepare(service.artifacts, view, ocr, user_notes=notes)
    if response_transport is None:
        # Keep the already-executed call-2 approval material reproducible after
        # the optional transport field was added to request profiles.
        prepared["profile"].pop("response_transport", None)
        kind = "full_production_request_low_reasoning"
        budget = PRODUCTION_BUDGET
        call_number = 2
    else:
        kind = "full_production_request_low_reasoning_streaming"
        budget = STREAMING_PRODUCTION_BUDGET
        call_number = 3
    material = {
        "schema_version": 1,
        "kind": kind,
        "source": {
            "task_id": PRODUCTION_SOURCE_TASK_ID,
            "operation_id": PRODUCTION_SOURCE_OPERATION_ID,
            "operation_state": operation.state,
            "access": "artifacts_read_only_no_resubmit",
            "plan_fingerprint": source_plan.fingerprint,
            "binding_sha256": digest(binding),
            "input_artifacts": [
                {
                    "role": item.role,
                    "artifact_id": item.artifact_id,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in binding.inputs
            ],
        },
        "model": analyzer.model,
        "endpoint_fingerprint": endpoint_fingerprint(),
        "request_profile": prepared["profile"],
        "request_sha256": prepared["request_sha256"],
        "request_size_bytes": prepared["request_size_bytes"],
        "schema_sha256": hashlib.sha256(
            canonical_json(prepared["request"]["text"]["format"]["schema"]).encode()
        ).hexdigest(),
        "budget": budget,
    }
    material["diagnostic_id"] = f"t030-vlm-call-{call_number}-" + material["request_sha256"][:20]
    return material, analyzer, service.artifacts, view, ocr, notes, prepared


def plan_production(value: str = PRODUCTION_VALIDATION_ROOT) -> dict:
    root = _root(value)
    material, *_ = _production_context()
    result = {**material, "plan_fingerprint": digest(material), "provider_calls": 0}
    root.mkdir(parents=True, exist_ok=True)
    (root / "plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def plan_production_streaming(value: str = STREAMING_PRODUCTION_VALIDATION_ROOT) -> dict:
    root = _root(value)
    material, *_ = _production_context(response_transport=STREAMING_RESPONSE_TRANSPORT)
    result = {**material, "plan_fingerprint": digest(material), "provider_calls": 0}
    root.mkdir(parents=True, exist_ok=True)
    (root / "plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _execute_production(value: str, fingerprint: str, *, response_transport=None, client=None) -> dict:
    root = _root(value)
    plan_path = root / "plan.json"
    if not plan_path.is_file():
        raise PipelineError("plan_missing", "Run the offline production-shape plan first")
    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    material, analyzer, store, view, ocr, notes, prepared = _production_context(
        response_transport=response_transport
    )
    expected = digest(material)
    if fingerprint != expected or saved != {**material, "plan_fingerprint": expected, "provider_calls": 0}:
        raise PipelineError("plan_changed", "Production-shape approval does not match the current exact request")
    attempt_path = root / "attempt.json"
    if attempt_path.exists():
        raise PipelineError("call_limit", "This production-shape diagnostic already crossed its dispatch boundary")

    owned_client = client is None
    if owned_client:
        client = build_llm_client(timeout=analyzer._transport_timeout())
    analyzer.client = client
    attempt_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "diagnostic_id": material["diagnostic_id"],
                "plan_fingerprint": expected,
                "dispatch_started_at": _timestamp(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        try:
            analysis = analyzer.analyze(
                store,
                view,
                ocr,
                user_notes=notes,
                prepared=prepared,
                operation_id=material["diagnostic_id"],
            )
        except VLMSubmissionError as exc:
            return _write_result(root, {"state": "outcome_unknown", "trace": exc.submission_trace})

        analysis_bytes = canonical_json(analysis).encode()
        analysis_path = root / "analysis-result.json"
        analysis_path.write_bytes(analysis_bytes)
        state = analysis["state"]
        error_code = analysis["error_code"]
        if analysis["actual"] and analysis["actual"]["cost_usd"] > material["budget"]["max_call_cost_usd"]:
            state = "failed"
            error_code = "budget_exceeded"
        return _write_result(
            root,
            {
                "state": state,
                "error_code": error_code,
                "validation_failure_stage": analysis["validation_failure_stage"],
                "validation_failure_reason": analysis["validation_failure_reason"],
                "trace": analysis["submission_trace"],
                "analysis_artifact": {
                    "path": "analysis-result.json",
                    "sha256": hashlib.sha256(analysis_bytes).hexdigest(),
                    "size_bytes": len(analysis_bytes),
                },
            },
        )
    finally:
        if owned_client:
            client.close()


def execute_production(value: str, fingerprint: str, *, client=None) -> dict:
    return _execute_production(value, fingerprint, client=client)


def execute_production_streaming(value: str, fingerprint: str, *, client=None) -> dict:
    return _execute_production(
        value,
        fingerprint,
        response_transport=STREAMING_RESPONSE_TRANSPORT,
        client=client,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "plan",
            "execute",
            "plan-production-low",
            "execute-production-low",
            "plan-production-low-streaming",
            "execute-production-low-streaming",
        ],
    )
    parser.add_argument("--validation-root")
    parser.add_argument("--approve")
    args = parser.parse_args()
    if args.validation_root:
        validation_root = args.validation_root
    elif "streaming" in args.command:
        validation_root = STREAMING_PRODUCTION_VALIDATION_ROOT
    elif "production" in args.command:
        validation_root = PRODUCTION_VALIDATION_ROOT
    else:
        validation_root = DEFAULT_VALIDATION_ROOT
    if args.command == "plan":
        result = plan(validation_root)
    elif args.command == "plan-production-low":
        result = plan_production(validation_root)
    elif args.command == "plan-production-low-streaming":
        result = plan_production_streaming(validation_root)
    else:
        if not args.approve:
            parser.error("--approve is required for execute")
        if args.command == "execute-production-low":
            result = execute_production(validation_root, args.approve)
        elif args.command == "execute-production-low-streaming":
            result = execute_production_streaming(validation_root, args.approve)
        else:
            result = execute(validation_root, args.approve)
        # Reload so any finally-updated timing fields are included in stdout.
        result = json.loads((_root(validation_root) / "result.json").read_text(encoding="utf-8"))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
