"""Only specifically approved revision prompts can differ from human boxes."""

from types import SimpleNamespace

import pytest
from PIL import Image

from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.pipeline import digest
from letsaigc.schemas.ui import UISegmentationPrompt
from letsaigc.vision.segmentation import _validate_prompts


@pytest.mark.parametrize("version,box,allowed", [
    ("selection-v1", (3, 3, 19, 11), False),
    ("revision-v1", (3, 3, 19, 11), True),
    ("revision-v1", (30, 3, 45, 11), False),
])
def test_revision_hint_needs_signed_parameters_and_stays_in_selection(version, box, allowed):
    prompt = UISegmentationPrompt(element_id="element", box=box)
    job = SimpleNamespace(prompts=[prompt], parameters_hash=digest({
        "prompt_version": version, "prompts": [prompt.model_dump(mode="json")],
    }))
    geometry = (None, None, {"element": (2, 2, 18, 10)}, [(0, 0, 32, 32)], {"element"}, {})
    with Image.new("RGB", (64, 64)) as image:
        if allowed:
            assert _validate_prompts(job, image, geometry) == [(prompt, box)]
        else:
            with pytest.raises(PipelineError):
                _validate_prompts(job, image, geometry)
