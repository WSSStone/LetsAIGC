"""T019 frozen v2 content contract, with future DTO/semantic conformance gates."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from letsaigc.schemas import agent
from letsaigc.schemas.pipeline import digest

ROOT = Path(__file__).resolve().parents[2]


def fixture():
    return json.loads((ROOT / "tests/fixtures/contracts/ui-edit-contracts.json").read_text())


def validator():
    path = ROOT / "specs/013-game-ui-analysis/contracts/masked-generation-plan-v2.schema.json"
    schema = json.loads(path.read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_v2_contract_and_v1_reader_remain_separate():
    data = fixture()
    validator().validate(data["masked_plan"])
    assert digest(data["masked_plan"]) == data["masked_fingerprint"]
    with pytest.raises(ValueError):
        agent.GenerationPlan.model_validate(data["masked_plan"])
    from letsaigc.schemas.pipeline import canonical_json

    legacy = agent.GenerationPlan.model_validate(data["generation_v1"])
    assert canonical_json(legacy) == data["v1_canonical"]
    assert digest(legacy) == data["v1_fingerprint"]


@pytest.mark.parametrize("field", [
    "image_ref", "mask_ref", "canonical_ref", "canonical_edit_mask_ref", "selection_ref",
    "selection_revision", "selection_hash", "view_transform_ref", "crop", "resize", "pad",
    "width", "height", "canonical_width", "canonical_height", "mask_policy_version",
])
def test_every_frozen_mask_input_is_required(field):
    value = fixture()["masked_plan"]
    del value["image_mask"][field]
    with pytest.raises(ValidationError):
        validator().validate(value)


@pytest.mark.parametrize("field,value", [
    ("mask_role", "alpha"), ("recipe_supports_mask", False), ("width", 0), ("height", 8193),
    ("width", True), ("selection_revision", -1), ("selection_hash", "unknown"),
    ("crop", [0, 0, 64]), ("resize", [0, 48]), ("pad", [-1, 0, 0, 0]),
])
def test_invalid_mask_structure_is_rejected(field, value):
    plan = fixture()["masked_plan"]
    plan["image_mask"][field] = value
    with pytest.raises(ValidationError):
        validator().validate(plan)


@pytest.mark.parametrize("field", [
    "image_ref", "mask_ref", "canonical_ref", "canonical_edit_mask_ref", "selection_ref", "view_transform_ref",
])
def test_ref_roles_and_hashes_are_part_of_approval_material(field):
    plan = fixture()["masked_plan"]
    changed = copy.deepcopy(plan)
    changed["image_mask"][field]["sha256"] = "f" * 64
    validator().validate(changed)
    assert digest(changed) != digest(plan)
    changed["image_mask"][field]["role"] = "rect_crop"
    with pytest.raises(ValidationError):
        validator().validate(changed)


@pytest.mark.parametrize("field,value", [
    ("selection_revision", 1), ("crop", [1, 0, 64, 48]),
    ("mask_policy_version", "canonical-edit-v2"),
])
def test_selection_and_preprocessing_changes_require_new_fingerprint(field, value):
    plan = fixture()["masked_plan"]
    old = digest(plan)
    plan["image_mask"][field] = value
    assert digest(plan) != old


def test_t020_v2_dto_enforces_hash_scope_and_envelope_consistency():
    if not hasattr(agent, "MaskedGenerationPlan"):
        pytest.xfail("T020: MaskedGenerationPlan DTO missing; artifact/pixel checks remain T025/T026")
    plan = fixture()["masked_plan"]
    parsed = agent.MaskedGenerationPlan.model_validate(plan)
    validator().validate(parsed.model_dump(mode="json"))
    variants = []
    changed = copy.deepcopy(plan)
    changed["image_mask"]["selection_hash"] = "f" * 64
    variants.append(changed)
    changed = copy.deepcopy(plan)
    changed["image_mask"]["image_ref"]["task_id"] = "foreign"
    changed["image_mask"]["image_ref"]["key"] = "foreign/input/image.png"
    variants.append(changed)
    changed = copy.deepcopy(plan)
    changed["envelope"]["model"] = "different-model"
    variants.append(changed)
    for invalid in variants:
        with pytest.raises(ValueError):
            agent.MaskedGenerationPlan.model_validate(invalid)


@pytest.mark.parametrize("field,value", [("model", "replacement-model"), ("recipe", "replacement-recipe")])
def test_model_or_recipe_replacement_changes_approval_material(field, value):
    plan = fixture()["masked_plan"]
    old = digest(plan)
    plan[field] = plan["envelope"][field] = value
    validator().validate(plan)
    assert digest(plan) != old


def test_mutable_values_are_distinct_from_expanding_approved_parameter_limits():
    plan = fixture()["masked_plan"]
    assert plan["envelope"]["mutable_parameters"] == ["negative_prompt", "prompt", "seed"]
    old = digest(plan)
    plan["envelope"]["max_width"] = 128
    assert digest(plan) != old
    resized = digest(plan)
    plan["envelope"]["budget"]["max_total_cost_usd"] = 2
    assert digest(plan) != resized
