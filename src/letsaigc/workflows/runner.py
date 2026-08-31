from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..comfy import ComfyClient
from ..config import load_catalog
from ..errors import ReadinessError, RuntimeExecutionError
from ..models import ModelManager
from ..paths import find_repo_root, local_path
from ..policy import assert_model_allowed, verify_sha256
from ..tracking.manifest import add_output, create_manifest, save_manifest
from ..tracking.mlflow_store import log_manifest
from .contracts import prepare_workflow


class WorkflowRunner:
    def __init__(self, client: ComfyClient | None = None) -> None:
        self.client = client or ComfyClient()

    def run(self, workflow_id: str, overrides: dict[str, Any] | None = None) -> dict:
        contract, graph, values = prepare_workflow(workflow_id, overrides)
        catalog = load_catalog()
        lookup = catalog.by_id()
        lanes = [lookup[model_id].license.lane for model_id in contract.models]
        root = find_repo_root()
        api_path = root / contract.api_workflow
        model_hashes: dict[str, list[str]] = {}
        manager = ModelManager(catalog=catalog)
        for model_id in contract.models:
            model = lookup[model_id]
            assert_model_allowed(model, operation="inference")
            results = manager.verify_model(model)
            if not all(result["ok"] for result in results):
                raise ReadinessError(f"Required workflow model is missing: {model_id}")
            model_hashes[model_id] = [result["sha256"] for result in results]
        adapter_hashes: dict[str, str] = {}
        for dependency in contract.adapters:
            adapter_path = local_path("models", "loras", dependency.path)
            verify_sha256(adapter_path, dependency.sha256)
            adapter_hashes[dependency.path] = dependency.sha256
        manifest = create_manifest(
            kind="inference",
            parameters={"workflow_id": workflow_id, **values},
            license_lanes=lanes,
            source={
                "workflow_api_sha256": hashlib.sha256(api_path.read_bytes()).hexdigest(),
                "workflow_contract": f"workflows/contracts/{workflow_id}.yaml",
                "model_sha256": model_hashes,
                "adapter_sha256": adapter_hashes,
            },
        )
        manifest.governance.validations["contract"] = True
        manifest.status = "validated"
        save_manifest(manifest)
        try:
            prompt_id = self.client.submit(graph)
            manifest.tracking["comfy_prompt_id"] = prompt_id
            manifest.status = "queued"
            save_manifest(manifest)
            manifest.status = "running"
            save_manifest(manifest)
            history = self.client.wait(
                prompt_id,
                timeout_seconds=contract.resource_budget.timeout_seconds,
            )
            for node in history.get("outputs", {}).values():
                for image in node.get("images", []):
                    if image.get("type", "output") != "output":
                        continue
                    output = local_path("output", image.get("subfolder", ""), image["filename"])
                    if output.is_file():
                        add_output(manifest, output)
            if not manifest.outputs:
                raise RuntimeExecutionError("ComfyUI completed without a discoverable output file")
            for output in manifest.outputs:
                verify_sha256(Path(output.path), output.sha256)
            manifest.governance.validations["hashes"] = True
            mlflow_run_id = log_manifest(
                manifest.run_id,
                {"kind": "inference", "workflow_id": workflow_id},
                {"output_count": float(len(manifest.outputs))},
            )
            if mlflow_run_id:
                manifest.tracking["mlflow_run_id"] = mlflow_run_id
            manifest.status = "succeeded"
        except Exception as exc:
            manifest.status = "failed"
            manifest.error = {"type": type(exc).__name__, "message": str(exc)}
            save_manifest(manifest)
            raise
        save_manifest(manifest)
        return json.loads(manifest.model_dump_json())
