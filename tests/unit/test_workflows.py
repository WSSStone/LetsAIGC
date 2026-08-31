from __future__ import annotations

import os

import pytest

from letsaigc.errors import ValidationError
from letsaigc.workflows import prepare_workflow


def test_workflow_overrides_are_bound_by_json_pointer() -> None:
    contract, graph, values = prepare_workflow(
        "sd15-smoke", {"prompt": "sprite sword", "seed": 7, "width": 640}
    )
    assert contract.id == "sd15-smoke"
    assert values["seed"] == 7
    assert graph["6"]["inputs"]["text"] == "sprite sword"
    assert graph["3"]["inputs"]["seed"] == 7
    assert graph["5"]["inputs"]["width"] == 640
    assert graph["4"]["inputs"]["ckpt_name"] == f"sd15{os.sep}v1-5-pruned-emaonly.safetensors"


def test_workflow_rejects_out_of_budget_dimensions() -> None:
    with pytest.raises(ValidationError, match="input validation"):
        prepare_workflow("sd15-smoke", {"width": 2048})


def test_workflow_rejects_unknown_inputs() -> None:
    with pytest.raises(ValidationError, match="input validation"):
        prepare_workflow("sd15-smoke", {"surprise": True})
