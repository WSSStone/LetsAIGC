from __future__ import annotations

import subprocess
from pathlib import Path

from ..errors import ReadinessError
from ..paths import find_repo_root, local_path


def serve(*, host: str = "127.0.0.1", port: int = 8188) -> int:
    if host != "127.0.0.1":
        raise ValueError("ComfyUI host must remain 127.0.0.1")
    comfy = local_path("runtime", "ComfyUI")
    main = comfy / "main.py"
    if not main.is_file():
        raise ReadinessError("ComfyUI is not deployed; bootstrap the comfy component first")
    root = find_repo_root()
    command = [
        "conda",
        "run",
        "--no-capture-output",
        "-n",
        "letsaigc-comfy",
        "python",
        str(main),
        "--listen",
        host,
        "--port",
        str(port),
        "--extra-model-paths-config",
        str(comfy / "extra_model_paths.yaml"),
        "--output-directory",
        str(local_path("output")),
        "--input-directory",
        str(local_path("input")),
    ]
    return subprocess.run(command, cwd=Path(root), check=False).returncode
