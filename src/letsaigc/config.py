from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from .errors import ValidationError
from .paths import find_repo_root
from .schemas import (
    Catalog,
    DramaProject,
    EvaluationSuite,
    LicensePolicy,
    ProviderCatalog,
    SpriteProfile,
    TrainingConfig,
    WorkflowContract,
)


def _dotenv_values() -> dict[str, str]:
    """Parse the Git-ignored repository .env file; process environment wins over it."""
    env_file = find_repo_root() / ".env"
    values: dict[str, str] = {}
    if not env_file.is_file():
        return values
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, raw = stripped.split("=", 1)
        values[name.strip()] = raw.strip().strip('"').strip("'")
    return values


def get_setting(name: str, default: str | None = None) -> str | None:
    """Return a runtime setting from the process environment, then .env; empty means unset."""
    value = os.getenv(name)
    if value is None or not value.strip():
        value = _dotenv_values().get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def get_int_setting(name: str, default: int) -> int:
    raw = get_setting(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValidationError(f"Environment variable {name} must be an integer: {raw!r}") from exc


def get_url_setting(name: str, default: str | None = None) -> str | None:
    """Return a URL-like setting, rejecting values without a scheme and host/path body."""
    value = get_setting(name, default)
    if value is None:
        return None
    if "://" not in value or value.split("://", 1)[1] == "":
        raise ValidationError(f"Environment variable {name} must be an absolute URL")
    return value


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


def load_sprite_profile(path: Path | str) -> SpriteProfile:
    return load_typed(path, SpriteProfile)


def load_drama_project(path: Path | str) -> DramaProject:
    return load_typed(path, DramaProject)


def load_license_policy() -> LicensePolicy:
    path = find_repo_root() / "configs/policies/license-policy.yaml"
    return load_typed(path, LicensePolicy)


def load_provider_catalog() -> ProviderCatalog:
    path = find_repo_root() / "configs/providers/openai-models.yaml"
    return load_typed(path, ProviderCatalog)


def write_schema(model: type[BaseModel], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model.model_json_schema(), indent=2), encoding="utf-8")
