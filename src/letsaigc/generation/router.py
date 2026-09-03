from __future__ import annotations

from ..errors import ReadinessError, ValidationError
from ..schemas import BackendCapability, BackendName, GenerationIntent


def default_capabilities() -> list[BackendCapability]:
    return [
        BackendCapability(
            backend="comfy",
            intents=[
                GenerationIntent.text_to_image,
                GenerationIntent.image_to_image,
                GenerationIntent.text_to_video,
                GenerationIntent.image_to_video,
                GenerationIntent.sprite_sequence,
                GenerationIntent.short_drama,
            ],
            media_kinds=["image", "video"],
            supports_alpha=False,
            stability="stable",
            cost_type="local_gpu",
        ),
        BackendCapability(
            backend="openai",
            intents=[GenerationIntent.text_to_image, GenerationIntent.image_to_image],
            media_kinds=["image"],
            supports_alpha=True,
            sizes=["1024x1024", "1024x1536", "1536x1024"],
            stability="stable",
            cost_type="remote_usd",
        ),
    ]


class CapabilityRouter:
    def __init__(self, capabilities: list[BackendCapability] | None = None) -> None:
        self.capabilities = capabilities or default_capabilities()

    def route(self, intent: GenerationIntent, requested: BackendName = BackendName.auto) -> BackendCapability:
        candidates = [item for item in self.capabilities if item.available and intent in item.intents]
        if requested != BackendName.auto:
            candidates = [item for item in candidates if item.backend == requested.value]
            if not candidates:
                if requested == BackendName.openai and intent in {
                    GenerationIntent.text_to_video,
                    GenerationIntent.image_to_video,
                }:
                    raise ValidationError("Remote video generation is not supported in v1")
                raise ReadinessError(f"Requested backend cannot satisfy intent: {requested.value}")
        if not candidates:
            raise ReadinessError(f"No qualified backend can satisfy intent: {intent.value}")
        candidates.sort(key=lambda item: (item.backend != "comfy", item.stability != "stable"))
        return candidates[0]
