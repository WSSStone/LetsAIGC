from __future__ import annotations

import base64
import hashlib
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from ..agent.approval import canonical_json
from ..agent.responses import load_openai_api_key, redact_secret
from ..assets.resolver import validate_image_bytes
from ..config import load_provider_catalog
from ..errors import ReadinessError, RuntimeExecutionError, ValidationError
from ..generation.pricing import calculate_actual_cost, ensure_current, load_pricing
from ..paths import local_path
from ..policy import verify_sha256
from ..schemas import GenerationIntent, GenerationPlan
from .base import GenerationResult

MODEL_SNAPSHOT = "gpt-image-2-2026-04-21"
ALLOWED_SIZES = {"1024x1024", "1024x1536", "1536x1024"}
ALLOWED_QUALITY = {"low", "medium", "high"}
ALLOWED_BACKGROUND = {"auto", "opaque", "transparent"}


def _default_client():
    key = load_openai_api_key()
    if not key:
        raise ReadinessError("OPENAI_API_KEY is not configured")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ReadinessError("OpenAI Python package is not installed in letsaigc-core") from exc
    return OpenAI(api_key=key, max_retries=0, timeout=300)


class OpenAIImageBackend:
    name = "openai"

    def __init__(self, client: Any | None = None) -> None:
        self.client = client

    def _client(self):
        if self.client is None:
            self.client = _default_client()
        return self.client

    def execute(self, plan: GenerationPlan, *, iteration_id: str) -> GenerationResult:
        provider = next(
            (entry for entry in load_provider_catalog().models if entry.snapshot == MODEL_SNAPSHOT),
            None,
        )
        if provider is None or provider.license_lane != "production":
            raise ReadinessError("Pinned GPT Image 2 provider entry is missing or not production-qualified")
        pricing = load_pricing()
        ensure_current(pricing)
        if provider.pricing_id != pricing.id or plan.parameters.get("pricing_id") != pricing.id:
            raise ReadinessError("Approved OpenAI pricing version is missing or has changed")
        if plan.backend != "openai" or plan.model != MODEL_SNAPSHOT:
            raise ValidationError("OpenAI image backend requires the pinned GPT Image 2 snapshot")
        size = str(plan.parameters.get("size", "1024x1024"))
        quality = str(plan.parameters.get("quality", "low"))
        background = str(plan.parameters.get("background", "auto"))
        if size not in ALLOWED_SIZES or quality not in ALLOWED_QUALITY or background not in ALLOWED_BACKGROUND:
            raise ValidationError("OpenAI image parameters exceed the approved v1 envelope")
        width, height = (int(part) for part in size.split("x"))
        if (
            width > plan.envelope.max_width or height > plan.envelope.max_height
            or quality != plan.envelope.quality or background != plan.envelope.background
            or set(plan.envelope.mutable_parameters) - {"prompt"}
        ):
            raise ValidationError("OpenAI request does not match the approved envelope")
        prompt = str(plan.parameters.get("prompt", "")).strip()
        if not prompt or len(prompt) > 2000:
            raise ValidationError("Image prompt must contain 1..2000 characters")
        request_record = {
            "model": MODEL_SNAPSHOT,
            "intent": plan.intent.value,
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "background": background,
            "pricing_id": pricing.id,
            "input_hashes": [item.sha256 for item in plan.input_assets],
            "input_derived_hashes": [item.derived_sha256 for item in plan.input_assets],
        }
        try:
            if plan.intent == GenerationIntent.text_to_image:
                response = self._client().images.generate(
                    model=MODEL_SNAPSHOT,
                    prompt=prompt,
                    n=1,
                    size=size,
                    quality=quality,
                    background=background,
                    output_format="png",
                )
            elif plan.intent == GenerationIntent.image_to_image:
                if not plan.input_assets:
                    raise ValidationError("Image editing requires at least one verified input")
                for asset in plan.input_assets:
                    verify_sha256(Path(asset.derived_path), asset.derived_sha256)
                with ExitStack() as stack:
                    images = [stack.enter_context(Path(item.derived_path).open("rb")) for item in plan.input_assets]
                    response = self._client().images.edit(
                        model=MODEL_SNAPSHOT,
                        image=images,
                        prompt=prompt,
                        n=1,
                        size=size,
                        quality=quality,
                        background=background,
                        output_format="png",
                    )
            else:
                raise ValidationError("OpenAI backend supports image generation and editing only")
            encoded = response.data[0].b64_json
            if not encoded:
                raise RuntimeExecutionError("Provider response did not include base64 image data")
            content = base64.b64decode(encoded, validate=True)
            _, width, height, _ = validate_image_bytes(content, "image/png")
            if width > plan.envelope.max_width or height > plan.envelope.max_height:
                raise RuntimeExecutionError("Provider image exceeded the approved output dimensions")
            output_dir = local_path("agent", "tasks", plan.task_id, "iterations", iteration_id)
            output_dir.mkdir(parents=True, exist_ok=True)
            output = output_dir / "primary.png"
            output.write_bytes(content)
            usage = self._usage_dict(getattr(response, "usage", None))
            actual_cost = calculate_actual_cost(pricing, usage)
            request_id = getattr(response, "_request_id", None) or getattr(response, "request_id", None)
            return GenerationResult(
                outputs=[output],
                request_id=str(request_id) if request_id else None,
                model_snapshot=MODEL_SNAPSHOT,
                usage=usage,
                actual_cost_usd=actual_cost,
                request_hash=hashlib.sha256(canonical_json(request_record)).hexdigest(),
            )
        except (ValidationError, RuntimeExecutionError):
            raise
        except Exception as exc:
            raise RuntimeExecutionError(
                "OpenAI image request failed",
                details={"error": redact_secret(str(exc), load_openai_api_key())},
            ) from exc

    @staticmethod
    def _usage_dict(value: Any) -> dict[str, Any]:
        if value is None:
            raise RuntimeExecutionError("Image usage is missing; cost requires reconciliation")
        if hasattr(value, "model_dump"):
            raw = value.model_dump(mode="json")
        elif isinstance(value, dict):
            raw = value
        else:
            raise RuntimeExecutionError("Image usage is invalid; cost requires reconciliation")
        input_details = raw.get("input_tokens_details") or raw.get("input_tokens_detail") or {}
        if not {"text_tokens", "image_tokens"}.issubset(input_details) or "output_tokens" not in raw:
            raise RuntimeExecutionError("Image usage is incomplete; cost requires reconciliation")
        return {
            "text_input_tokens": int(input_details.get("text_tokens", 0)),
            "image_input_tokens": int(input_details.get("image_tokens", 0)),
            "image_output_tokens": int(raw.get("output_tokens", raw.get("image_output_tokens", 0))),
            "raw": raw,
        }
