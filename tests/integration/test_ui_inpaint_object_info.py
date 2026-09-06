"""Read-only ComfyUI object_info evidence for the native inpaint bundle.

The live probe has two explicit gates: pytest's ``--ui-live`` option and the
task-specific ``LETSAIGC_UI_INPAINT_OBJECT_INFO_LIVE=1`` environment variable.
The ordinary contract test below is local-only and never contacts ComfyUI.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

import psutil
import pytest

from letsaigc.comfy import ComfyClient
from letsaigc.comfy.runtime import comfy_lock_path
from letsaigc.config import load_workflow_contract, load_yaml
from letsaigc.errors import RuntimeExecutionError
from letsaigc.paths import find_repo_root
from letsaigc.workflows.compiler import load_recipe

ROOT = find_repo_root()
LOCK_PATH = comfy_lock_path(ROOT)
EVIDENCE_DIR = ROOT / ".local" / "validation" / "ui-analysis"
EVIDENCE_PATH = EVIDENCE_DIR / "inpaint-object-info.json"
INPAINT_NODES = {
    "CheckpointLoaderSimple",
    "LoadImage",
    "ImageToMask",
    "VAEEncodeForInpaint",
    "CLIPTextEncode",
    "KSampler",
    "VAEDecode",
    "SaveImage",
}
EXPECTED_MODULES = {
    "CheckpointLoaderSimple": "nodes",
    "LoadImage": "nodes",
    "ImageToMask": "comfy_extras.nodes_mask",
    "VAEEncodeForInpaint": "nodes",
    "CLIPTextEncode": "nodes",
    "KSampler": "nodes",
    "VAEDecode": "nodes",
    "SaveImage": "nodes",
}
PORT_TYPES = {
    "ImageToMask": {"image": "IMAGE", "channel": "COMBO"},
    "VAEEncodeForInpaint": {"pixels": "IMAGE", "mask": "MASK", "vae": "VAE", "grow_mask_by": "INT"},
    "CLIPTextEncode": {"clip": "CLIP", "text": "STRING"},
    "KSampler": {
        "model": "MODEL",
        "positive": "CONDITIONING",
        "negative": "CONDITIONING",
        "latent_image": "LATENT",
        "seed": "INT",
        "steps": "INT",
        "cfg": "FLOAT",
    },
    "VAEDecode": {"samples": "LATENT", "vae": "VAE"},
    "SaveImage": {"images": "IMAGE", "filename_prefix": "STRING"},
}


def _runtime_checkout_provenance(runtime_path: Path, expected_commit: str) -> dict[str, Any]:
    """Read-only proof that the running service source is the locked checkout."""
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=runtime_path,
        capture_output=True,
        text=True,
        check=False,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=runtime_path,
        capture_output=True,
        text=True,
        check=False,
    )
    exact_tag = subprocess.run(
        ["git", "describe", "--tags", "--exact-match", "HEAD"],
        cwd=runtime_path,
        capture_output=True,
        text=True,
        check=False,
    )
    actual_commit = revision.stdout.strip() if revision.returncode == 0 else None
    dirty = status.stdout.strip()
    return {
        "path": str(runtime_path.relative_to(ROOT)),
        "expected_commit": expected_commit,
        "actual_commit": actual_commit,
        "commit_matches": actual_commit == expected_commit,
        "git_status": dirty,
        "clean": status.returncode == 0 and not dirty,
        "exact_tag": exact_tag.stdout.strip() if exact_tag.returncode == 0 else None,
        "git_commands_ok": revision.returncode == 0 and status.returncode == 0,
    }


def _expected_environment_python(environment: str) -> Path:
    completed = subprocess.run(
        [
            "conda",
            "run",
            "--no-capture-output",
            "-n",
            environment,
            "python",
            "-c",
            "import sys; print(sys.executable)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Could not resolve the locked conda environment: {completed.stderr.strip()}")
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("The locked conda environment returned no Python executable")
    return Path(lines[-1]).resolve()


def _running_service_provenance(lock: dict[str, Any], runtime_path: Path) -> dict[str, Any]:
    """Find the process serving the locked port without exposing its environment."""
    expected_python = _expected_environment_python(str(lock["environment"]))
    candidates: list[dict[str, Any]] = []
    for process in psutil.process_iter(["pid", "ppid", "name", "cmdline", "cwd"]):
        try:
            cmdline = process.info.get("cmdline") or []
            if not any("main.py" in part for part in cmdline):
                continue
            main_argument = next(part for part in cmdline if "main.py" in part)
            process_cwd = process.info.get("cwd")
            if not process_cwd or (Path(process_cwd) / main_argument).resolve() != runtime_path / "main.py":
                continue
            try:
                listen_index = cmdline.index("--listen")
                port_index = cmdline.index("--port")
                actual_listen = cmdline[listen_index + 1]
                actual_port = int(cmdline[port_index + 1])
            except (ValueError, IndexError, TypeError):
                continue
            if actual_listen != lock["listen"] or actual_port != lock["port"]:
                continue
            startup = lock.get("startup", {})
            startup_args = startup.get("args", []) if isinstance(startup, dict) else []
            if not isinstance(startup_args, list) or any(arg not in cmdline for arg in startup_args):
                continue
            environment = process.environ()
            executable = Path(process.exe()).resolve()
            candidates.append(
                {
                    "pid": process.pid,
                    "ppid": process.info.get("ppid"),
                    "name": process.info.get("name"),
                    "cwd": process.info.get("cwd"),
                    "executable": str(executable),
                    "expected_executable": str(expected_python),
                    "executable_matches_environment": executable == expected_python,
                    "listen": actual_listen,
                    "port": actual_port,
                    "startup_flags": {arg: arg in cmdline for arg in startup_args},
                    "environment": {
                        "CONDA_DEFAULT_ENV": environment.get("CONDA_DEFAULT_ENV"),
                        "CONDA_PREFIX": environment.get("CONDA_PREFIX"),
                    },
                    "environment_matches_lock": environment.get("CONDA_DEFAULT_ENV") == lock["environment"],
                }
            )
        except (KeyError, OSError, TypeError, psutil.Error, StopIteration):
            continue

    listeners: list[int] = []
    try:
        connections = psutil.net_connections(kind="tcp")
    except psutil.AccessDenied:
        # macOS may deny socket enumeration to a same-user test process. lsof
        # gives the same read-only listener identity without exposing sockets.
        lsof = subprocess.run(
            [
                "lsof",
                "-nP",
                "-a",
                f"-iTCP:{lock['port']}",
                "-sTCP:LISTEN",
                "-Fp",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        listeners = [
            int(line[1:])
            for line in lsof.stdout.splitlines()
            if line.startswith("p") and line[1:].isdigit()
        ]
    else:
        for connection in connections:
            address = connection.laddr
            if (
                connection.status == psutil.CONN_LISTEN
                and getattr(address, "ip", None) == lock["listen"]
                and getattr(address, "port", None) == lock["port"]
                and connection.pid is not None
            ):
                listeners.append(connection.pid)
    return {
        "platform": f"{platform.system()}-{platform.machine()}",
        "expected_environment": lock["environment"],
        "expected_executable": str(expected_python),
        "listeners": sorted(set(listeners)),
        "candidates": candidates,
        "listener_matches_candidate": bool(
            set(listeners) & {candidate["pid"] for candidate in candidates}
        ),
    }


def _validate_native_node_contracts(object_info: dict[str, Any]) -> dict[str, Any]:
    modules = {
        node: object_info.get(node, {}).get("python_module")
        for node in sorted(INPAINT_NODES)
        if isinstance(object_info.get(node), dict)
    }
    missing_nodes = sorted(INPAINT_NODES - set(object_info))
    wrong_modules = sorted(
        node for node, expected in EXPECTED_MODULES.items() if modules.get(node) != expected
    )
    external_modules = sorted(
        {
            str(value.get("python_module"))
            for value in object_info.values()
            if isinstance(value, dict)
            and not (
                value.get("python_module") == "nodes"
                or str(value.get("python_module", "")).startswith("comfy_extras.")
            )
        }
    )

    channel = object_info.get("ImageToMask", {}).get("input", {}).get("required", {}).get("channel")
    channel_options: list[str] = []
    channel_is_single = False
    if isinstance(channel, list) and len(channel) == 2:
        if channel[0] == "COMBO" and isinstance(channel[1], dict):
            channel_options = [str(value) for value in channel[1].get("options", [])]
            channel_is_single = channel[1].get("multiselect") is False
        elif isinstance(channel[0], list):
            channel_options = [str(value) for value in channel[0]]
            channel_is_single = True

    grow_mask = (
        object_info.get("VAEEncodeForInpaint", {})
        .get("input", {})
        .get("required", {})
        .get("grow_mask_by")
    )
    grow_mask_supports_zero = (
        isinstance(grow_mask, list)
        and len(grow_mask) == 2
        and grow_mask[0] == "INT"
        and isinstance(grow_mask[1], dict)
        and grow_mask[1].get("min", 0) <= 0 <= grow_mask[1].get("max", 0)
    )
    missing_ports: dict[str, list[str]] = {}
    wrong_port_types: dict[str, dict[str, Any]] = {}
    for node, expected_ports in PORT_TYPES.items():
        required = object_info.get(node, {}).get("input", {}).get("required", {})
        missing = sorted(set(expected_ports) - set(required))
        if missing:
            missing_ports[node] = missing
        wrong = {
            port: required[port][0]
            for port, expected in expected_ports.items()
            if port in required and required[port][0] != expected
        }
        if wrong:
            wrong_port_types[node] = wrong
    return {
        "missing_nodes": missing_nodes,
        "python_modules": modules,
        "wrong_python_modules": wrong_modules,
        "external_python_modules": external_modules,
        "missing_ports": missing_ports,
        "wrong_port_types": wrong_port_types,
        "image_to_mask_channel": {
            "options": channel_options,
            "single_select": channel_is_single,
            "red_supported": "red" in channel_options,
        },
        "vae_encode_for_inpaint_grow_mask_by": {
            "schema": grow_mask,
            "supports_zero": grow_mask_supports_zero,
        },
    }


def _service_matches_lock(service: dict[str, Any], lock: dict[str, Any]) -> bool:
    """Require the listener PID and process provenance to identify one process."""
    listeners = set(service.get("listeners", []))
    return any(
        candidate.get("pid") in listeners
        and candidate.get("executable_matches_environment") is True
        and candidate.get("environment_matches_lock") is True
        and candidate.get("listen") == lock.get("listen")
        and candidate.get("port") == lock.get("port")
        for candidate in service.get("candidates", [])
    )


def _node_types(document: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(document, dict):
        for key in ("class_type", "type", "node_type"):
            value = document.get(key)
            if isinstance(value, str):
                found.add(value)
        for value in document.values():
            found.update(_node_types(value))
    elif isinstance(document, list):
        for value in document:
            found.update(_node_types(value))
    return found


def _load_bundle() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    recipe = load_recipe("sdxl-inpaint")
    contract = load_workflow_contract("sdxl-inpaint")
    ui = json.loads((ROOT / "workflows" / "ui" / "sdxl-inpaint.json").read_text(encoding="utf-8"))
    api = json.loads((ROOT / "workflows" / "api" / "sdxl-inpaint.json").read_text(encoding="utf-8"))
    return recipe.model_dump(mode="json"), contract.model_dump(mode="json"), ui, api


def _write_evidence(payload: dict[str, Any]) -> Path:
    """Write evidence without replacing an earlier probe result."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    destination = EVIDENCE_PATH
    index = 1
    while destination.exists():
        destination = EVIDENCE_PATH.with_name(f"{EVIDENCE_PATH.stem}-{index}{EVIDENCE_PATH.suffix}")
        index += 1
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def test_inpaint_four_artifacts_are_synchronized_without_live_service() -> None:
    """Validate the committed bundle locally; this is not live object_info evidence."""
    recipe, contract, ui, api = _load_bundle()
    lock = load_yaml(LOCK_PATH)

    assert recipe["id"] == contract["id"] == "sdxl-inpaint"
    assert recipe["version"] == contract["version"]
    assert recipe["models"] == contract["models"] == ["sdxl-base-1.0"]
    assert set(recipe["allowed_nodes"]) == set(contract["nodes"]) == INPAINT_NODES
    assert set(_node_types(ui)) >= INPAINT_NODES
    assert set(_node_types(api)) >= INPAINT_NODES
    assert contract["ui_workflow"] == "workflows/ui/sdxl-inpaint.json"
    assert contract["api_workflow"] == "workflows/api/sdxl-inpaint.json"
    assert contract["defaults"]["mask"] == {
        "channel": "red",
        "polarity": "white_is_edit",
        "grow_mask_by": 0,
    }
    assert api["13"]["class_type"] == "ImageToMask"
    assert api["13"]["inputs"]["channel"] == "red"
    assert api["11"]["class_type"] == "VAEEncodeForInpaint"
    assert api["11"]["inputs"]["grow_mask_by"] == 0
    assert lock["listen"] == "127.0.0.1"
    assert lock["port"] == 8188
    assert lock["tag"] == "v0.34.2"
    assert len(lock["commit"]) == 40
    assert lock["custom_nodes"] == []


@pytest.mark.ui_live
def test_opt_in_live_inpaint_object_info_probe_records_evidence() -> None:
    """GET the pinned loopback object_info endpoint and record readiness."""
    if os.getenv("LETSAIGC_UI_INPAINT_OBJECT_INFO_LIVE") != "1":
        pytest.skip("Set LETSAIGC_UI_INPAINT_OBJECT_INFO_LIVE=1 for the dedicated read-only probe")

    base_evidence: dict[str, Any] = {
        "schema_version": 1,
        "probe": "ui-inpaint-object-info",
        "access": "GET /object_info only",
        "status": "not_ready",
        "lock_path": str(LOCK_PATH.relative_to(ROOT)),
        "contract": "sdxl-inpaint",
    }
    try:
        lock = load_yaml(LOCK_PATH)
        base_evidence["lock"] = {
            "tag": lock.get("tag"),
            "commit": lock.get("commit"),
            "listen": lock.get("listen"),
            "port": lock.get("port"),
            "environment": lock.get("environment"),
            "platform": lock.get("platform"),
            "device": lock.get("device"),
            "startup": lock.get("startup"),
            "custom_nodes": lock.get("custom_nodes"),
        }
        if lock.get("listen") != "127.0.0.1" or lock.get("port") != 8188:
            base_evidence["reason"] = "lock_endpoint_is_not_the_pinned_loopback"
            evidence_path = _write_evidence(base_evidence)
            pytest.skip(f"Pinned Comfy endpoint is unavailable in lock: {evidence_path}")
        raw_runtime_path = lock.get("path")
        runtime_path = (ROOT / str(raw_runtime_path)).resolve() if raw_runtime_path else ROOT
        if not isinstance(raw_runtime_path, str) or not raw_runtime_path or not runtime_path.is_relative_to(ROOT):
            base_evidence["reason"] = "locked_comfy_runtime_path_is_unsafe"
            evidence_path = _write_evidence(base_evidence)
            pytest.fail(f"Pinned Comfy runtime path is unsafe: {evidence_path}")
        if not runtime_path.is_dir():
            base_evidence["reason"] = "locked_comfy_runtime_directory_missing"
            base_evidence["runtime_path"] = str(runtime_path.relative_to(ROOT))
            evidence_path = _write_evidence(base_evidence)
            pytest.skip(f"Pinned Comfy runtime is not installed: {evidence_path}")

        checkout = _runtime_checkout_provenance(runtime_path, str(lock.get("commit", "")))
        base_evidence["checkout"] = checkout
        if not checkout["git_commands_ok"] or not checkout["commit_matches"] or not checkout["clean"]:
            evidence_path = _write_evidence(base_evidence)
            pytest.fail(f"Pinned Comfy checkout provenance is invalid: {evidence_path}")
    except (OSError, TypeError, ValueError, KeyError) as exc:
        base_evidence["reason"] = "lock_resource_invalid_or_unavailable"
        base_evidence["error_type"] = type(exc).__name__
        evidence_path = _write_evidence(base_evidence)
        pytest.skip(f"Pinned Comfy lock resource is unavailable: {evidence_path}")

    client = ComfyClient(f"http://{lock['listen']}:{lock['port']}", timeout=3.0)
    try:
        object_info = client.object_info()
    except RuntimeExecutionError as exc:
        base_evidence["reason"] = "comfy_object_info_unavailable"
        base_evidence["error_type"] = type(exc).__name__
        base_evidence["error"] = str(exc)
        evidence_path = _write_evidence(base_evidence)
        pytest.skip(f"Pinned Comfy object_info is not ready: {evidence_path}")

    if not isinstance(object_info, dict):
        base_evidence["reason"] = "object_info_response_is_not_an_object"
        evidence_path = _write_evidence(base_evidence)
        pytest.fail(f"Comfy object_info response is invalid; evidence: {evidence_path}")

    try:
        service = _running_service_provenance(lock, runtime_path)
    except (OSError, RuntimeError, psutil.Error) as exc:
        base_evidence["reason"] = "service_provenance_unavailable"
        base_evidence["error_type"] = type(exc).__name__
        base_evidence["error"] = str(exc)
        evidence_path = _write_evidence(base_evidence)
        pytest.skip(f"Could not inspect the running Comfy process: {evidence_path}")

    recipe, contract, _ui, _api = _load_bundle()
    expected_nodes = set(recipe["allowed_nodes"]) == set(contract["nodes"]) == INPAINT_NODES
    contracts = _validate_native_node_contracts(object_info)
    summary = {
        "count": len(object_info),
        "sha256": hashlib.sha256(
            json.dumps(object_info, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "required_nodes": sorted(INPAINT_NODES),
        **contracts,
    }
    service_matches = _service_matches_lock(service, lock)
    base_evidence.update({"object_info": summary, "contract_nodes_match": expected_nodes, "service": service})
    status = "ready" if (
        expected_nodes
        and not contracts["missing_nodes"]
        and not contracts["wrong_python_modules"]
        and not contracts["external_python_modules"]
        and not contracts["missing_ports"]
        and not contracts["wrong_port_types"]
        and contracts["image_to_mask_channel"]["red_supported"]
        and contracts["image_to_mask_channel"]["single_select"]
        and contracts["vae_encode_for_inpaint_grow_mask_by"]["supports_zero"]
        and service_matches
    ) else "not_ready"
    evidence_path = _write_evidence(base_evidence | {"status": status})
    assert expected_nodes
    assert not contracts["missing_nodes"], (
        f"Pinned Comfy object_info lacks inpaint nodes; evidence: {evidence_path}"
    )
    assert not contracts["wrong_python_modules"], (
        f"Pinned inpaint nodes are not native nodes; evidence: {evidence_path}"
    )
    assert not contracts["external_python_modules"], (
        f"Comfy loaded a third-party node module; evidence: {evidence_path}"
    )
    assert not contracts["missing_ports"] and not contracts["wrong_port_types"], (
        f"Pinned native node ports do not satisfy the inpaint contract; evidence: {evidence_path}"
    )
    assert contracts["image_to_mask_channel"]["red_supported"]
    assert contracts["image_to_mask_channel"]["single_select"]
    assert contracts["vae_encode_for_inpaint_grow_mask_by"]["supports_zero"]
    assert service_matches, f"Running Comfy process is not the locked service; evidence: {evidence_path}"
