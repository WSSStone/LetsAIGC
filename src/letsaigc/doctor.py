from __future__ import annotations

import json
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil

from .models import ModelManager
from .paths import find_repo_root, local_path


@dataclass
class Check:
    id: str
    status: str
    message: str
    details: dict | None = None


def _command(name: str) -> Check:
    path = shutil.which(name)
    return Check(f"tool.{name}", "pass" if path else "warn", path or f"{name} not found")


def _gpu() -> Check:
    command = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.free,driver_version",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
        name, total, free, driver = [
            part.strip() for part in completed.stdout.splitlines()[0].split(",")
        ]
        total_mib, free_mib = int(total), int(free)
        status = "pass" if total_mib >= 10_000 else "fail"
        return Check(
            "hardware.gpu",
            status,
            f"{name}: {total_mib} MiB total, {free_mib} MiB free",
            {"name": name, "total_mib": total_mib, "free_mib": free_mib, "driver": driver},
        )
    except (OSError, subprocess.SubprocessError, IndexError, ValueError) as exc:
        return Check("hardware.gpu", "fail", "NVIDIA GPU query failed", {"error": str(exc)})


def _conda_envs() -> Check:
    try:
        completed = subprocess.run(
            ["mamba", "env", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        paths = json.loads(completed.stdout).get("envs", [])
        names = {Path(path).name.lower() for path in paths}
        required = {"letsaigc-core", "letsaigc-comfy", "letsaigc-train-sdxl"}
        missing = sorted(required - names)
        return Check(
            "runtime.conda_envs",
            "pass" if not missing else "warn",
            "all environments present" if not missing else f"missing: {', '.join(missing)}",
            {"missing": missing},
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return Check(
            "runtime.conda_envs", "fail", "mamba environment query failed", {"error": str(exc)}
        )


def _port(host: str, port: int) -> Check:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        listening = sock.connect_ex((host, port)) == 0
    return Check(
        f"service.{port}",
        "pass" if listening else "warn",
        f"{host}:{port} is {'reachable' if listening else 'not running'}",
    )


def run_doctor() -> dict:
    root = find_repo_root()
    memory = psutil.virtual_memory()
    disk_target = local_path()
    disk_target.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(disk_target)
    checks = [
        Check(
            "platform.os", "pass" if platform.system() == "Windows" else "warn", platform.platform()
        ),
        Check(
            "platform.python",
            "pass" if sys.version_info[:2] == (3, 12) else "warn",
            sys.version.split()[0],
        ),
        Check(
            "hardware.ram",
            "pass" if memory.total >= 60 * 1024**3 else "fail",
            f"{memory.total / 1024**3:.1f} GiB total, {memory.available / 1024**3:.1f} GiB available",
        ),
        Check(
            "storage.local",
            "pass" if disk.free >= 100 * 1024**3 else "warn",
            f"{disk.free / 1024**3:.1f} GiB free at {disk_target}",
        ),
        _gpu(),
        *[_command(name) for name in ("git", "git-lfs", "mamba", "ffmpeg")],
        _conda_envs(),
        _port("127.0.0.1", 8188),
        _port("127.0.0.1", 5000),
    ]
    manager = ModelManager()
    installed = sum(1 for item in manager.list() if item["installed"])
    checks.append(Check("models.catalog", "pass", f"catalog valid; {installed} model(s) installed"))
    checks.append(Check("repo.root", "pass", str(root)))
    statuses = {check.status for check in checks}
    overall = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    return {"status": overall, "checks": [asdict(check) for check in checks]}
