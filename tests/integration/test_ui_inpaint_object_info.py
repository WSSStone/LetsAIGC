"""Read-only ComfyUI object_info evidence for the native inpaint bundle.

The live probe has two explicit gates: pytest's ``--ui-live`` option and the
task-specific ``LETSAIGC_UI_INPAINT_OBJECT_INFO_LIVE=1`` environment variable.
The ordinary contract test below is local-only and never contacts ComfyUI.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from letsaigc.comfy import ComfyClient
from letsaigc.config import load_workflow_contract, load_yaml
from letsaigc.errors import RuntimeExecutionError
from letsaigc.paths import find_repo_root
from letsaigc.workflows.compiler import load_recipe

ROOT = find_repo_root()
LOCK_PATH = ROOT / "configs" / "runtime" / "comfyui.lock.yaml"
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
            "custom_nodes": lock.get("custom_nodes"),
        }
        if lock.get("listen") != "127.0.0.1" or lock.get("port") != 8188:
            base_evidence["reason"] = "lock_endpoint_is_not_the_pinned_loopback"
            evidence_path = _write_evidence(base_evidence)
            pytest.skip(f"Pinned Comfy endpoint is unavailable in lock: {evidence_path}")
        runtime_path = ROOT / str(lock.get("path", ""))
        if not runtime_path.is_dir():
            base_evidence["reason"] = "locked_comfy_runtime_directory_missing"
            base_evidence["runtime_path"] = str(runtime_path.relative_to(ROOT))
            evidence_path = _write_evidence(base_evidence)
            pytest.skip(f"Pinned Comfy runtime is not installed: {evidence_path}")
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

    recipe, contract, _ui, _api = _load_bundle()
    expected_nodes = set(recipe["allowed_nodes"]) == set(contract["nodes"]) == INPAINT_NODES
    missing_nodes = sorted(INPAINT_NODES - set(object_info))
    node_modules = {
        node: object_info.get(node, {}).get("python_module")
        for node in sorted(INPAINT_NODES)
        if isinstance(object_info.get(node), dict)
    }
    wrong_modules = sorted(node for node, module in node_modules.items() if module != "nodes")
    summary = {
        "count": len(object_info),
        "sha256": hashlib.sha256(
            json.dumps(object_info, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "required_nodes": sorted(INPAINT_NODES),
        "missing_nodes": missing_nodes,
        "python_modules": node_modules,
        "wrong_python_modules": wrong_modules,
    }
    base_evidence.update({"object_info": summary, "contract_nodes_match": expected_nodes})
    status = "ready" if not missing_nodes and not wrong_modules else "not_ready"
    evidence_path = _write_evidence(base_evidence | {"status": status})
    assert expected_nodes
    assert not missing_nodes, f"Pinned Comfy object_info lacks inpaint nodes; evidence: {evidence_path}"
    assert not wrong_modules, f"Pinned inpaint nodes are not native nodes; evidence: {evidence_path}"
