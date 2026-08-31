from __future__ import annotations

import json

import jsonschema

from letsaigc.config import (
    load_catalog,
    load_evaluation_suite,
    load_license_policy,
    load_training_config,
    load_workflow_contract,
)
from letsaigc.paths import find_repo_root


def test_catalog_profiles_and_files_are_closed() -> None:
    catalog = load_catalog()
    ids = [model.id for model in catalog.models]
    assert len(ids) == len(set(ids))
    assert catalog.profiles
    for profile_ids in catalog.profiles.values():
        assert set(profile_ids) <= set(ids)
    for model in catalog.models:
        assert model.format == "safetensors"
        assert all(item.target_path.endswith(".safetensors") for item in model.files)


def test_every_workflow_has_ui_api_and_valid_defaults() -> None:
    root = find_repo_root()
    for path in sorted((root / "workflows/contracts").glob("*.yaml")):
        contract = load_workflow_contract(path.stem)
        assert (root / contract.ui_workflow).is_file()
        assert (root / contract.api_workflow).is_file()
        json.loads((root / contract.ui_workflow).read_text(encoding="utf-8"))
        json.loads((root / contract.api_workflow).read_text(encoding="utf-8"))
        jsonschema.validate(contract.defaults, contract.input_schema)
        for dependency in contract.adapters:
            assert (root / dependency.record).is_file()


def test_training_profiles_preserve_low_vram_invariants() -> None:
    root = find_repo_root()
    for path in (root / "configs/training").glob("sdxl-lora-*.yaml"):
        config = load_training_config(path)
        assert config.train_batch_size == 1
        assert config.network_dim == config.network_alpha == 8
        assert config.train_text_encoder is False
        assert config.gradient_checkpointing is True
        assert config.save_model_as == "safetensors"


def test_manifest_design_schema_accepts_schema_shape() -> None:
    root = find_repo_root()
    schema = json.loads(
        (root / "specs/001-project-harness/contracts/run-manifest.schema.json").read_text()
    )
    jsonschema.Draft202012Validator.check_schema(schema)


def test_license_policy_denies_unknown_and_requires_human_approval() -> None:
    policy = load_license_policy()
    assert policy.deny_unknown_license is True
    assert policy.production_export.require_human_approval is True
    assert policy.production_export.allowed_lanes == ["production"]


def test_evaluation_suite_is_typed_and_complete() -> None:
    suite = load_evaluation_suite("2d-baseline")
    assert len(suite.cases) == 4
    assert len({case.id for case in suite.cases}) == 4
