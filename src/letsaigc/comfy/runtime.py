from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from ..config import load_yaml
from ..errors import ReadinessError
from ..paths import find_repo_root, local_path


def comfy_lock_path(root: Path | None = None) -> Path:
    """Return the ComfyUI lock selected for this host platform.

    The Windows lock remains the default for compatibility.  Apple silicon has
    a separate lock because its PyTorch wheel source and CPU startup flags are
    different from the CUDA deployment.
    """
    root = root or find_repo_root()
    platform_id = f"{platform.system()}-{platform.machine()}"
    if platform_id == "Darwin-arm64":
        return root / "configs" / "runtime" / "comfyui.macos-arm64.lock.yaml"
    return root / "configs" / "runtime" / "comfyui.lock.yaml"


def load_comfy_lock(root: Path | None = None) -> dict:
    """Load the host-selected ComfyUI runtime lock."""
    return load_yaml(comfy_lock_path(root))


def serve(*, host: str = "127.0.0.1", port: int = 8188) -> int:
    if host != "127.0.0.1":
        raise ValueError("ComfyUI host must remain 127.0.0.1")
    root = find_repo_root()
    lock = load_comfy_lock(root)
    if lock.get("listen") != host or lock.get("port") != port:
        raise ReadinessError("ComfyUI endpoint does not match the selected runtime lock")
    comfy = root / str(lock.get("path", ".local/runtime/ComfyUI"))
    main = comfy / "main.py"
    if not main.is_file():
        raise ReadinessError("ComfyUI is not deployed; bootstrap the comfy component first")
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
    startup = lock.get("startup", {})
    startup_args = startup.get("args", []) if isinstance(startup, dict) else []
    if not isinstance(startup_args, list) or not all(isinstance(arg, str) for arg in startup_args):
        raise ReadinessError("ComfyUI startup arguments are invalid in the selected runtime lock")
    command.extend(startup_args)
    return subprocess.run(command, cwd=Path(root), check=False).returncode
