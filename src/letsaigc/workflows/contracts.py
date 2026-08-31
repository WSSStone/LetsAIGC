from __future__ import annotations

import copy
import json
import os
from typing import Any

import jsonschema

from ..config import load_workflow_contract
from ..errors import ValidationError
from ..paths import find_repo_root
from ..schemas import WorkflowContract

COMFY_PATH_FIELDS = {"ckpt_name", "unet_name", "vae_name", "clip_name", "lora_name"}


def _normalize_comfy_paths(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in COMFY_PATH_FIELDS and isinstance(child, str):
                value[key] = child.replace("/", os.sep).replace("\\", os.sep)
            else:
                _normalize_comfy_paths(child)
    elif isinstance(value, list):
        for child in value:
            _normalize_comfy_paths(child)


def _set_pointer(document: dict, pointer: str, value: Any) -> None:
    if not pointer.startswith("/"):
        raise ValidationError(f"Binding is not a JSON pointer: {pointer}")
    current: Any = document
    parts = pointer.lstrip("/").split("/")
    for raw in parts[:-1]:
        key = raw.replace("~1", "/").replace("~0", "~")
        current = current[int(key)] if isinstance(current, list) else current[key]
    final = parts[-1].replace("~1", "/").replace("~0", "~")
    if isinstance(current, list):
        current[int(final)] = value
    else:
        current[final] = value


def prepare_workflow(
    workflow_id: str,
    overrides: dict[str, Any] | None = None,
) -> tuple[WorkflowContract, dict, dict]:
    contract = load_workflow_contract(workflow_id)
    values = {**contract.defaults, **(overrides or {})}
    try:
        jsonschema.validate(values, contract.input_schema)
    except jsonschema.ValidationError as exc:
        raise ValidationError(f"Workflow input validation failed: {exc.message}") from exc
    api_path = find_repo_root() / contract.api_workflow
    graph = json.loads(api_path.read_text(encoding="utf-8"))
    graph = copy.deepcopy(graph)
    for name, pointer in contract.bindings.items():
        if name in values:
            _set_pointer(graph, pointer, values[name])
    _normalize_comfy_paths(graph)
    return contract, graph, values
