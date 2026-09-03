from __future__ import annotations

import pytest

from letsaigc.errors import ValidationError
from letsaigc.generation import CapabilityRouter, default_capabilities
from letsaigc.schemas import BackendName, GenerationIntent


def test_auto_routing_is_local_first() -> None:
    result = CapabilityRouter(default_capabilities()).route(GenerationIntent.text_to_image)
    assert result.backend == "comfy"


def test_remote_video_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Remote video"):
        CapabilityRouter(default_capabilities()).route(
            GenerationIntent.text_to_video,
            BackendName.openai,
        )
