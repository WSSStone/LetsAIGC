"""One approved cloud guide/edit call, with durable results and no network recovery."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from datetime import UTC, datetime

import httpx
from PIL import Image

from ..agent.responses import build_llm_client, endpoint_fingerprint, load_llm_api_key
from ..assets.http_safety import private_http
from ..assets.resolver import AssetResolver
from ..errors import ValidationError
from ..generation.pricing import PricingEntry, calculate_actual_cost, calculate_luna_cost, ensure_current
from ..schemas.pipeline import ArtifactRef, Cost, canonical_json
from ..schemas.ui import UIAnalysisRequest, UIObservation, UIStepBinding
from ..schemas.ui_cloud import CloudEditPolicy, CloudEditRequest
from .contracts import Capability, Submission
from .errors import PipelineError


class CloudImageResponseError(ValueError):
    """Internal allowlisted classification; never use a provider error message."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _ImageResultResolver(AssetResolver):
    def _validate_public_url(self, source):
        # Validate every redirect as well as the initial signed URL. The base
        # resolver pins a public DNS address and retains the original TLS SNI.
        if (not isinstance(source, str) or len(source) > 8192
                or any(ord(char) <= 32 or ord(char) == 127 for char in source)
                or "\\" in source):
            raise CloudImageResponseError("image_download_invalid_url")
        return super()._validate_public_url(source)


def download_image_url(url, trace):
    """Collect one result, never submit generation or expose a signed URL."""
    trace["stages"].append("image_url_downloading")
    download = trace["download"] = {"attempt": 1, "http_statuses": []}

    def request_hook(request):
        # An independent client has no API credentials. Also discard cookies
        # received on redirects; a signed URL alone authorizes this download.
        for name in ("authorization", "proxy-authorization", "cookie"):
            request.headers.pop(name, None)

    def response_hook(response):
        download["http_statuses"].append(response.status_code)
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise CloudImageResponseError("image_download_content_encoding")

    def client_factory():
        return httpx.Client(
            trust_env=False, follow_redirects=False, transport=httpx.HTTPTransport(retries=0, trust_env=False),
            event_hooks={"request": [request_hook], "response": [response_hook]},
        )

    try:
        with private_http():
            # Existing intake boundary: 25 MiB, public HTTPS only, at most
            # three revalidated redirects, one 60-second transfer budget.
            output, _, _ = _ImageResultResolver(timeout=60, client_factory=client_factory)._fetch_https(url)
        download["bytes"] = len(output)
        trace["stages"].append("image_url_downloaded")
        return output
    except CloudImageResponseError:
        raise
    except httpx.TimeoutException:
        raise CloudImageResponseError("image_download_timeout") from None
    except httpx.TransportError:
        raise CloudImageResponseError("image_download_transport_error") from None
    except ValidationError as exc:
        # Only these locally defined messages select a code; none is logged.
        category = {
            "Image download exceeded its time budget": "image_download_timeout",
            "Remote image exceeds 25 MiB": "image_download_too_large",
            "Image URL request failed": "image_download_http_error",
            "Image URL exceeded 3 redirects": "image_download_redirect_limit",
            "Image redirect did not include a location": "image_download_invalid_redirect",
            "Image URL hostname resolution failed": "image_download_dns_error",
            "Image URL hostname did not resolve": "image_download_dns_error",
        }.get(str(exc), "image_download_unsafe_url")
        raise CloudImageResponseError(category) from None
    except (TypeError, ValueError):
        raise CloudImageResponseError("image_download_invalid_response") from None
    except Exception:
        raise CloudImageResponseError("image_download_failed") from None


def safe_id(value):
    if (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)
            and not value.startswith("sk-") and not (load_llm_api_key() and load_llm_api_key() in value)):
        return value
    return None


def record_headers(trace, response):
    headers = {}
    request_id = safe_id(response.headers.get("x-request-id"))
    if request_id:
        headers["x-request-id"] = request_id
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type in {"application/json", "text/event-stream", "text/html", "text/plain"}:
        headers["content-type"] = content_type
    retry = response.headers.get("retry-after", "")
    if re.fullmatch(r"[0-9]{1,8}", retry):
        headers["retry-after"] = retry
    trace.update(http_status=response.status_code, provider_request_id=request_id,
                 safe_response_headers=headers)
    if "response_headers_received" not in trace["stages"]:
        trace["stages"].append("response_headers_received")


def record_error(trace, exc):
    response = getattr(exc, "response", None)
    if response is not None:
        record_headers(trace, response)
    chain, current = [], exc
    while current is not None and len(chain) < 8 and current not in chain:
        chain.append(current)
        current = current.__cause__
    category = ("response_validation_or_persistence_error"
                if "response_headers_received" in trace["stages"] else "dispatch_error")
    for kind, label in ((httpx.ConnectTimeout, "connect_timeout"), (httpx.WriteTimeout, "write_timeout"),
                        (httpx.ReadTimeout, "read_timeout"), (httpx.PoolTimeout, "pool_timeout"),
                        (httpx.TransportError, "connection_error")):
        if any(isinstance(item, kind) for item in chain):
            category = label
            break
    if response is not None and response.status_code >= 400:
        category = "http_server_error" if response.status_code >= 500 else "http_rejection"
    if isinstance(exc, CloudImageResponseError):
        category = exc.code
    elif response is None and trace["stages"][-1] in {
        "response_body_parsing", "image_base64_decoding", "image_decoding", "image_integrity_check",
        "candidate_persisting", "output_persisting", "receipt_persisting", "usage_pricing",
    }:
        category = trace["stages"][-1] + "_failed"
    trace.update(error_category=category, failure_stage=trace["stages"][-1])


def token_count(value):
    if type(value) is not int or value < 0:
        raise ValueError("invalid_usage")
    return value


def measured_usage(value, stage):
    if value is None:
        return None
    raw = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    try:
        if stage == "cloud_guide":
            result = {"input_tokens": token_count(raw["input_tokens"]),
                      "cached_input_tokens": token_count(
                          (raw.get("input_tokens_details") or {}).get("cached_tokens", 0)),
                      "output_tokens": token_count(raw["output_tokens"])}
            if result["cached_input_tokens"] > result["input_tokens"]:
                return None
            return result
        details = raw["input_tokens_details"]
        return {"text_input_tokens": token_count(details["text_tokens"]),
                "image_input_tokens": token_count(details["image_tokens"]),
                "image_output_tokens": token_count(raw["output_tokens"])}
    except (KeyError, TypeError, ValueError):
        return None


class UICloudBackend:
    def __init__(self, service, stage, *, client=None):
        if stage not in {"cloud_guide", "cloud_inpaint"}:
            raise PipelineError("invalid_child")
        self.service, self.store, self.stage, self.client = service, service.artifacts, stage, client
        self.capability = Capability(id="ui." + stage)

    def request(self, plan):
        request = CloudEditRequest.model_validate_json(self.store.read(
            ArtifactRef.model_validate(plan.parameters["request_ref"])))
        if request.stage != self.stage:
            raise PipelineError("operation_scope")
        root = self.service.ledger.plan(plan.parameters["root_task_id"])
        root_request = UIAnalysisRequest.model_validate_json(self.store.read(
            ArtifactRef.model_validate(root.parameters["request_ref"])))
        frozen = CloudEditPolicy.model_validate_json(self.store.read(root_request.model_bindings["cloud_inpaint"]))
        if request.retry is not None:
            # Budget-only amendment is frozen in this child's exact approval;
            # validate_cloud_target checks ancestry and all generation inputs.
            frozen = frozen.model_copy(update={"image_budget_usd": plan.envelope.budget.max_iteration_cost_usd})
        if (request.policy.model_dump(exclude={"pricing_ref"}) != frozen.model_dump(exclude={"pricing_ref"})
                or request.policy.pricing_ref.sha256 != frozen.pricing_ref.sha256):
            raise PipelineError("dependency_changed")
        return request

    def preflight(self, plan):
        from ..ui_analysis.cloud_editing import validate_cloud_target

        validate_cloud_target(self.service, plan)
        request = self.request(plan)
        if request.policy.endpoint_fingerprint != endpoint_fingerprint():
            raise PipelineError("dependency_changed")
        pricing = PricingEntry.model_validate_json(self.store.read(request.policy.pricing_ref))
        ensure_current(pricing)
        calculate_luna_cost(pricing, {}, model=request.policy.vlm_model)
        if self.client is None and not load_llm_api_key():
            raise PipelineError("credentials_not_ready")
        with Image.open(io.BytesIO(self.store.read(request.image_ref))) as image:
            if image.format != "PNG" or image.size != (512, 512):
                raise PipelineError("invalid_input")
            image.verify()
        return request, pricing

    def prepare(self, operation_id, arguments):
        binding = UIStepBinding.model_validate(arguments["binding"])
        plan = self.service.ledger.plan(binding.task_id)
        request, pricing = self.preflight(plan)
        image = self.store.read(request.image_ref)
        if self.stage == "cloud_guide":
            payload = {"model": request.policy.vlm_model, "reasoning": {"effort": request.policy.reasoning},
                       "max_output_tokens": request.policy.max_output_tokens, "stream": True,
                       "input": [{"role": "user", "content": [
                           {"type": "input_text", "text": request.prompt},
                           {"type": "input_image", "detail": request.policy.image_detail,
                            "image_url": "data:image/png;base64," + base64.b64encode(image).decode("ascii")},
                       ]}]}
        else:
            payload = {"model": request.policy.model, "prompt": request.prompt,
                       "image": ("context.png", image, "image/png"), "n": request.policy.n,
                       "size": request.policy.size, "quality": request.policy.quality,
                       "background": request.policy.background, "output_format": request.policy.output_format}
        client = self.client or build_llm_client(timeout=httpx.Timeout(
            connect=request.policy.connect_timeout_seconds, write=request.policy.write_timeout_seconds,
            pool=request.policy.pool_timeout_seconds, read=request.policy.timeout_seconds))
        return {"plan": plan, "request": request, "pricing": pricing, "payload": payload, "client": client}

    def submit(self, operation_id, arguments):
        prepared = arguments.get("prepared") or self.prepare(operation_id, arguments)
        plan, request, client = (prepared[key] for key in ("plan", "request", "client"))
        trace = {"operation_id": operation_id, "client_request_id": operation_id, "attempt": 1,
                 "started_at": datetime.now(UTC).isoformat(), "stages": ["sdk_dispatch_started"],
                 "stage": self.stage, "profile": request.policy.model_dump(mode="json", exclude={"instruction"}),
                 "input_ref": request.image_ref.model_dump(mode="json"),
                 "provider_request_id": None, "provider_response_id": None}
        stream = None
        try:
            headers = {"X-Client-Request-Id": operation_id}
            if self.stage == "cloud_guide":
                raw = client.responses.with_raw_response.create(**prepared["payload"], extra_headers=headers)
                record_headers(trace, raw)
                stream = raw.parse()
                final = None
                for event in stream:
                    if event.type in {"response.created", "response.completed",
                                      "response.incomplete", "response.failed"}:
                        trace["provider_response_id"] = safe_id(event.response.id)
                    if event.type in {"response.completed", "response.incomplete", "response.failed"}:
                        final = event.response
                if final is None:
                    raise ValueError("missing_terminal_response")
                trace["stages"].append("response_parsed")
                usage = measured_usage(final.usage, self.stage)
                prompt = final.output_text
                valid = final.status == "completed" and isinstance(prompt, str) and 0 < len(prompt) <= 8000
                output = canonical_json({"prompt": prompt if valid else "", "generated": True}).encode()
                role, media_type = "cloud_guidance", "application/json"
            else:
                raw = client.images.with_raw_response.edit(**prepared["payload"], extra_headers=headers)
                record_headers(trace, raw)
                trace["stages"].append("response_body_parsing")
                final = raw.parse()
                trace["stages"].append("response_parsed")
                usage = measured_usage(getattr(final, "usage", None), self.stage)
                # Preserve usage before any content checks or filesystem write.
                trace["usage"] = usage
                data = getattr(final, "data", None)
                trace["image_count"] = len(data) if isinstance(data, list) else None
                if not isinstance(data, list) or len(data) != 1:
                    raise CloudImageResponseError("invalid_image_count")
                encoded = getattr(data[0], "b64_json", None)
                trace["url_present"] = bool(getattr(data[0], "url", None))
                trace["base64_present"] = isinstance(encoded, str) and bool(encoded)
                if not isinstance(encoded, str) or not encoded:
                    if not trace["url_present"]:
                        raise CloudImageResponseError("missing_image_base64")
                    trace["image_transport"] = "url"
                    output = download_image_url(data[0].url, trace)
                else:
                    trace["image_transport"] = "base64"
                    trace["encoded_bytes"] = len(encoded)
                    if len(encoded) > 32 * 1024 * 1024:
                        raise CloudImageResponseError("image_payload_too_large")
                    trace["stages"].append("image_base64_decoding")
                    output = base64.b64decode(encoded, validate=True)
                trace["decoded_bytes"] = len(output)
                # Retain bounded decoded bytes locally for diagnosis even if
                # format/dimensions fail. Never publish them as a valid image.
                trace["stages"].append("candidate_persisting")
                candidate = self.store.put(plan.task_id, operation_id, output, role="cloud_image_candidate",
                                           media_type="application/octet-stream")
                trace["candidate_ref"] = candidate.model_dump(mode="json")
                trace["stages"].append("image_decoding")
                with Image.open(io.BytesIO(output)) as pixels:
                    trace["image_format"] = (
                        pixels.format if pixels.format in {"PNG", "JPEG", "WEBP", "GIF"} else "other")
                    trace["image_size"] = list(pixels.size)
                    if pixels.format != "PNG":
                        raise CloudImageResponseError("invalid_image_format")
                    if pixels.size != (1024, 1024):
                        raise CloudImageResponseError("invalid_image_dimensions")
                    trace["stages"].append("image_integrity_check")
                    pixels.verify()
                valid, role, media_type = True, "image", "image/png"
            trace["stages"].append("output_persisting")
            output_ref = self.store.put(plan.task_id, operation_id, output, role=role, media_type=media_type,
                                        source_ids=[request.image_ref.artifact_id])
            trace["output_ref"] = output_ref.model_dump(mode="json")
            trace["stages"].append("usage_pricing")
            actual = None
            if usage is not None:
                cost = (calculate_luna_cost(prepared["pricing"], usage, model=request.policy.vlm_model)
                        if self.stage == "cloud_guide" else calculate_actual_cost(prepared["pricing"], usage))
                actual = Cost(cost_usd=cost).model_dump(mode="json")
            trace.update(usage=usage, actual=actual, finished_at=datetime.now(UTC).isoformat())
            # Missing usage retains the actual image but cannot silently become a zero charge.
            result = {"state": "unknown" if actual is None else "succeeded" if valid else "failed",
                      "actual": actual, "usage": usage, "output_ref": output_ref.model_dump(mode="json"),
                      "submission_trace": trace, "composition": request.context["composition"]}
            trace["stages"].append("receipt_persisting")
            ref = self.store.put(plan.task_id, operation_id, canonical_json(result).encode(), role="cloud_receipt")
            return self._submission(ref, result)
        except Exception as exc:
            record_error(trace, exc)
            trace["finished_at"] = datetime.now(UTC).isoformat()
            error = PipelineError("cloud_submission_unknown")
            error.submission_trace = trace
            raise error from None
        finally:
            for resource in (stream, client if self.client is None else None):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass

    def _submission(self, ref, result):
        trace = result["submission_trace"]
        key = trace.get("provider_request_id") or trace.get("provider_response_id")
        return Submission(request_id=key or "local-result-" + ref.operation_id,
                          metadata={"result_ref": ref.model_dump(mode="json"), **result,
                                    "request_id_source": "provider" if key else "local_result"})

    def recover(self, operation_id):
        """Recover only an already persisted result. Never call a provider here."""
        operation = self.service.ledger.get(operation_id)
        # Resolve using a scoped reference, including Windows long-path support.
        base = ArtifactRef(task_id=operation.task_id, operation_id=operation_id,
                           artifact_id="receipt-probe", role="cloud_receipt", sha256="0" * 64,
                           key=f"{operation.task_id}/{operation_id}/cloud_receipt/{'0' * 64}", size_bytes=0,
                           media_type="application/json")
        files = [path for path in self.store.resolve(base).parent.glob("*")
                 if re.fullmatch(r"[a-f0-9]{64}", path.name)]
        if len(files) != 1:
            return None
        data = files[0].read_bytes()
        if hashlib.sha256(data).hexdigest() != files[0].name:
            raise PipelineError("artifact_changed")
        ref = base.model_copy(update={"sha256": files[0].name,
                                      "artifact_id": "asset-" + hashlib.sha256(
                                          (files[0].name + "cloud_receipt").encode()).hexdigest()[:48],
                                      "key": base.key.rsplit("/", 1)[0] + "/" + files[0].name,
                                      "size_bytes": len(data)})
        result = json.loads(self.store.read(ref))
        if result["submission_trace"]["operation_id"] != operation_id:
            raise PipelineError("operation_scope")
        self.store.read(ArtifactRef.model_validate(result["output_ref"]))
        return self._submission(ref, result)

    def inspect(self, submission):
        ref = ArtifactRef.model_validate(submission.metadata["result_ref"])
        result = json.loads(self.store.read(ref))
        return UIObservation(state=result["state"], result_ref=ref,
                             actual=Cost.model_validate(result["actual"]) if result["actual"] is not None else None)

    def collect(self, submission):
        result = json.loads(self.store.read(ArtifactRef.model_validate(submission.metadata["result_ref"])))
        ref = ArtifactRef.model_validate(result["output_ref"])
        return [(ref.role, self.store.read(ref), ref.media_type)]
