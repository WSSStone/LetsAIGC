from __future__ import annotations

import subprocess

from ..config import get_int_setting, get_setting
from .mlflow_store import artifact_root, tracking_uri


def serve_mlflow(*, host: str | None = None, port: int | None = None) -> int:
    host = host or get_setting("MLFLOW_HOST", "127.0.0.1")
    port = port if port is not None else get_int_setting("MLFLOW_PORT", 5000)
    if host != "127.0.0.1":
        raise ValueError("MLflow host must remain 127.0.0.1")
    artifacts = artifact_root()
    command = [
        "mlflow",
        "server",
        "--backend-store-uri",
        tracking_uri(),
        "--default-artifact-root",
        str(artifacts),
        "--host",
        host,
        "--port",
        str(port),
        "--workers",
        "1",
    ]
    return subprocess.run(command, check=False).returncode
