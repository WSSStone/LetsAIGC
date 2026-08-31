from __future__ import annotations

import subprocess

from ..paths import local_path
from .mlflow_store import tracking_uri


def serve_mlflow(*, host: str = "127.0.0.1", port: int = 5000) -> int:
    if host != "127.0.0.1":
        raise ValueError("MLflow host must remain 127.0.0.1")
    artifacts = local_path("mlflow", "artifacts")
    artifacts.mkdir(parents=True, exist_ok=True)
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
