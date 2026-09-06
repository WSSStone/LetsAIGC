"""Runtime DTO contracts for the T020 editing boundary."""

import copy
import json
from pathlib import Path

import pytest

from letsaigc.schemas import agent, ui
from letsaigc.schemas.pipeline import digest

ROOT = Path(__file__).resolve().parents[2]


def fixture() -> dict:
    return json.loads((ROOT / "tests/fixtures/contracts/ui-edit-contracts.json").read_text())


def test_selection_and_candidate_are_strict_runtime_models():
    data = fixture()
    selection = ui.UISelection.model_validate(data["selection"])
    assert selection.sources[0].target_regions[0].kind == "bbox"

    ref = data["masked_plan"]["image_mask"]["selection_ref"]
    candidate = {
        "candidate_id": "candidate-1",
        "task_id": ref["task_id"],
        "selection_ref": ref,
        "preview_ref": {**ref, "role": "selection_preview"},
        "evidence_ref": {**ref, "role": "selection_evidence"},
        "origin": "agent_proposed",
        "revision": 0,
        "state": "proposed",
    }
    parsed = ui.UISelectionCandidate.model_validate(candidate)
    assert parsed.preview_ref.role == "selection_preview"

    invalid = copy.deepcopy(candidate)
    invalid["preview_ref"]["task_id"] = "other-task"
    with pytest.raises(ValueError):
        ui.UISelectionCandidate.model_validate(invalid)


@pytest.mark.parametrize(
    "change",
    [
        {"target_regions": [{"kind": "bbox", "xyxy": [3, 0, 2, 1]}]},
        {"target_regions": [{"kind": "bbox", "xyxy": [0, 0, 1, 1]}, {"kind": "bbox", "xyxy": [0, 0, 1, 1]}]},
        {"keep_elements": ["same"], "remove_elements": ["same"]},
        {"target_regions": [{"kind": "element", "element_id": "icon"}]},
    ],
)
def test_selection_rejects_invalid_geometry_or_unbound_elements(change):
    value = fixture()["selection"]
    value["sources"][0].update(change)
    with pytest.raises(ValueError):
        ui.UISelection.model_validate(value)


def test_masked_plan_is_separate_from_v1_and_freezes_mask_material():
    data = fixture()
    plan = agent.MaskedGenerationPlan.model_validate(data["masked_plan"])
    assert plan.schema_version == 2
    assert plan.image_mask.selection_hash == plan.image_mask.selection_ref.sha256
    assert digest(plan) == data["masked_fingerprint"]
    with pytest.raises(ValueError):
        agent.GenerationPlan.model_validate(data["masked_plan"])
    assert digest(agent.GenerationPlan.model_validate(data["generation_v1"])) == data["v1_fingerprint"]


def test_image_mask_uses_artifact_ref_schema_and_includes_padding_in_prepared_size():
    value = fixture()["masked_plan"]["image_mask"]
    schema = agent.ImageMaskBinding.model_json_schema()
    assert schema["properties"]["image_ref"]["$ref"].endswith("ArtifactRef")

    value["resize"] = [32, 24]
    value["pad"] = [1, 2, 3, 4]
    value["width"] = 36
    value["height"] = 30
    binding = agent.ImageMaskBinding.model_validate(value)
    assert binding.width == 36 and binding.height == 30

    value["width"] = 35
    with pytest.raises(ValueError):
        agent.ImageMaskBinding.model_validate(value)


@pytest.mark.parametrize("field,value", [("image_ref", None), ("mask_ref", "artifact"), ("selection_ref", object())])
def test_image_mask_rejects_non_artifact_references(field, value):
    data = fixture()["masked_plan"]["image_mask"]
    data[field] = value
    with pytest.raises((TypeError, ValueError)):
        agent.ImageMaskBinding.model_validate(data)


@pytest.mark.parametrize(
    "change",
    [
        {"selection_hash": "f" * 64},
        {"image_ref": {"task_id": "foreign", "key": "foreign/input/image.json"}},
        {"canonical_edit_mask_ref": {"role": "canonical"}},
    ],
)
def test_masked_plan_rejects_scope_hash_and_role_drift(change):
    value = fixture()["masked_plan"]
    for key, changed in change.items():
        if isinstance(changed, dict):
            value["image_mask"][key].update(changed)
        else:
            value["image_mask"][key] = changed
    with pytest.raises(ValueError):
        agent.MaskedGenerationPlan.model_validate(value)


def test_edit_step_binding_requires_a_frozen_selection():
    value = fixture()["masked_plan"]["image_mask"]
    with pytest.raises(ValueError):
        ui.UIStepBinding(
            task_id="edit-child",
            step_id="gpu",
            capability="ui.inpaint",
            inputs=[value["image_ref"]],
        )
    binding = ui.UIStepBinding(
        task_id="edit-child",
        step_id="gpu",
        capability="ui.inpaint",
        inputs=[value["image_ref"]],
        selection_ref=value["selection_ref"],
        selection_revision=value["selection_revision"],
        selection_hash=value["selection_hash"],
    )
    assert binding.selection_hash == binding.selection_ref.sha256


def test_legacy_step_binding_hash_omits_new_empty_selection_fields():
    from letsaigc.schemas.pipeline import ArtifactRef

    ref = ArtifactRef(
        task_id="legacy",
        artifact_id="input",
        key="legacy/input/input.bin",
        sha256="a" * 64,
        size_bytes=1,
        media_type="application/octet-stream",
        role="original",
        operation_id="op",
    )
    binding = ui.UIStepBinding(task_id="legacy", step_id="ocr", capability="ui.ocr", inputs=[ref])
    assert binding.input_hash == "bba7340130bce7883a14920dde506a7ef79890e5f486ef5374fd624f3f2f0225"
    assert "selection_ref" not in binding.model_dump(mode="json", exclude={"outputs"})
