from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from .paths import find_repo_root
from .schemas import Catalog, EvaluationSuite, LicensePolicy, TrainingConfig, WorkflowContract


def load_yaml(path: Path | str) -> dict[str, Any]:
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a mapping in {source}")
    return value


def load_typed[T: BaseModel](path: Path | str, model: type[T]) -> T:
    return model.model_validate(load_yaml(path))


def load_catalog(path: Path | None = None) -> Catalog:
    return load_typed(path or find_repo_root() / "configs/models/catalog.yaml", Catalog)


def load_workflow_contract(workflow_id: str) -> WorkflowContract:
    path = find_repo_root() / "workflows/contracts" / f"{workflow_id}.yaml"
    return load_typed(path, WorkflowContract)


def load_training_config(path: Path | str) -> TrainingConfig:
    return load_typed(path, TrainingConfig)


def load_evaluation_suite(suite_id: str) -> EvaluationSuite:
    path = find_repo_root() / "configs/eval" / f"{suite_id}.yaml"
    return load_typed(path, EvaluationSuite)


def load_license_policy() -> LicensePolicy:
    path = find_repo_root() / "configs/policies/license-policy.yaml"
    return load_typed(path, LicensePolicy)


def write_schema(model: type[BaseModel], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model.model_json_schema(), indent=2), encoding="utf-8")
