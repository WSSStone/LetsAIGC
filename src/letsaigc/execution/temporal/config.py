from __future__ import annotations

import importlib.util
import socket
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import Field, model_validator

from ...paths import find_repo_root, local_path
from ...pipelines.errors import PipelineError
from ...schemas.pipeline import Identifier, PipelineModel


class TemporalConfig(PipelineModel):
    schema_version: Literal[1] = 1
    address: str = "127.0.0.1:7233"
    namespace: Identifier = "default"
    task_queue: Identifier = "letsaigc-pipelines-v1"
    max_concurrent_activities: int = Field(default=4, ge=1, le=32)
    activity_timeout_seconds: int = Field(default=600, ge=1, le=86400)
    poll_seconds: float = Field(default=2, gt=0, le=60)
    observations_per_run: int = Field(default=100, ge=1, le=1000)
    mlflow_enabled: bool = False

    @model_validator(mode="after")
    def loopback_only(self):
        parsed = urlsplit("http://" + self.address)
        if (
            parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not parsed.port
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Temporal v1 requires a loopback host:port")
        return self


def load_config(path: Path | None = None) -> TemporalConfig:
    config_path = path or find_repo_root() / "configs/runtime/temporal.yaml"
    if not config_path.exists():
        return TemporalConfig()
    try:
        return TemporalConfig.model_validate(yaml.safe_load(config_path.read_text(encoding="utf-8")))
    except (ValueError, OSError) as exc:
        raise PipelineError("invalid_config", "Temporal configuration is invalid") from exc


def runtime_root() -> Path:
    return local_path("pipelines")


def require_sdk() -> None:
    if importlib.util.find_spec("temporalio") is None:
        raise PipelineError("temporal_unavailable", "Install the optional temporal extra in letsaigc-core")


def diagnose(config: TemporalConfig | None = None) -> dict:
    config = config or load_config()
    try:
        sdk_version = version("temporalio")
    except PackageNotFoundError:
        sdk_version = None
    parsed = urlsplit("http://" + config.address)
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=0.3):
            reachable = True
    except OSError:
        reachable = False
    return {
        "sdk_version": sdk_version,
        "service_reachable": reachable,
        "address": config.address,
        "namespace": config.namespace,
        "task_queue": config.task_queue,
        "status": "pass" if sdk_version and reachable else "warn",
    }
