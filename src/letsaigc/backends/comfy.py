from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import psutil

from ..comfy import ComfyClient
from ..config import load_catalog
from ..errors import ReadinessError, RuntimeExecutionError, ValidationError
from ..media import discover_declared_outputs, inspect_media_tools, probe_media
from ..models import ModelManager
from ..paths import find_repo_root, local_path
from ..policy import assert_model_allowed, verify_sha256
from ..schemas import GenerationPlan, MediaOutputDeclaration
from ..tracking.hardware import GpuMemorySampler
from ..tracking.manifest import add_output, create_manifest, save_manifest
from ..workflows.compiler import WorkflowCompiler, load_recipe
from .base import GenerationResult


class ComfyBackend:
    name = "comfy"

    def __init__(self, client: ComfyClient | None = None, compiler: WorkflowCompiler | None = None) -> None:
        self.client = client or ComfyClient()
        self.compiler = compiler or WorkflowCompiler()

    def execute(self, plan: GenerationPlan, *, iteration_id: str) -> GenerationResult:
        if plan.backend != "comfy" or not plan.recipe:
            raise ValidationError("Comfy backend requires a recipe-based plan")
        recipe = load_recipe(plan.recipe)
        if len(plan.input_assets) > 1:
            raise ValidationError("Local Comfy recipes support at most one image input")
        required = recipe.resource_budget
        local_path().mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(local_path()).free < required["free_disk_gib"] * 1024**3:
            raise ReadinessError("Insufficient disk space for the approved recipe")
        if psutil.virtual_memory().total < required["ram_gib"] * 1024**3:
            raise ReadinessError("Insufficient system RAM for the approved recipe")
        catalog = load_catalog()
        lookup = catalog.by_id()
        manager = ModelManager(catalog=catalog)
        model_hashes: dict[str, list[str]] = {}
        for model_id in recipe.models:
            model = lookup.get(model_id)
            if model is None:
                raise ReadinessError(f"Recipe references unknown model: {model_id}")
            assert_model_allowed(model, operation="inference")
            results = manager.verify_model(model)
            if not all(item["ok"] for item in results):
                profiles = ", ".join(model.profiles)
                raise ReadinessError(
                    f"Required model is missing or invalid: {model_id}. Run: letsaigc models sync {profiles}"
                )
            model_hashes[model_id] = [item["sha256"] for item in results]
        uploaded: list[str] = []
        for asset in plan.input_assets:
            verify_sha256(Path(asset.derived_path), asset.derived_sha256)
            uploaded.append(
                self.client.upload_image(
                    Path(asset.derived_path),
                    subfolder=f"letsaigc-agent/{plan.session_id}/{plan.task_id}",
                )
            )
        compiled = self.compiler.compile(
            plan,
            uploaded_images=uploaded,
            object_info=self.client.object_info(),
        )
        run_kind = "video_generation" if "video" in plan.intent.value else "inference"
        manifest = create_manifest(
            kind=run_kind,
            parameters={"agent_task_id": plan.task_id, "iteration_id": iteration_id, **plan.parameters},
            license_lanes=[lookup[model_id].license.lane for model_id in recipe.models],
            source={
                "recipe_id": recipe.id,
                "recipe_version": recipe.version,
                "comfy_commit": subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=local_path("runtime", "ComfyUI"),
                    capture_output=True, text=True, check=False,
                ).stdout.strip(),
                "compiled_graph_sha256": compiled.graph_sha256,
                "compiled_contract_sha256": compiled.contract_sha256,
                "compiled_graph_path": compiled.local_graph_path,
                "compiled_contract_path": compiled.local_contract_path,
                "model_sha256": model_hashes,
                "input_sha256": [asset.sha256 for asset in plan.input_assets],
                "input_derived_sha256": [asset.derived_sha256 for asset in plan.input_assets],
            },
        )
        manifest.governance.validations.update({
            "contract": True, "recipe": True,
            "runtime_qualification": recipe.stability == "stable" and all(
                lookup[model_id].runtime_status == "qualified" for model_id in recipe.models
            ),
        })
        committed = True
        for relative in (f"configs/workflows/recipes/{recipe.id}.yaml", recipe.base_workflow):
            tracked = subprocess.run(
                ["git", "show", f"HEAD:{relative}"], cwd=find_repo_root(), capture_output=True, check=False,
            )
            current = (find_repo_root() / relative).read_bytes().replace(b"\r\n", b"\n")
            committed &= tracked.returncode == 0 and tracked.stdout.replace(b"\r\n", b"\n") == current
        manifest.governance.validations["recipe_committed"] = committed
        if run_kind == "video_generation":
            media_tools = inspect_media_tools()
            if not media_tools.ready:
                raise ReadinessError("Required FFmpeg decoding/encoding capabilities are missing")
            manifest.environment["media_tools"] = media_tools.as_dict()
        manifest.status = "validated"
        save_manifest(manifest)
        started = time.monotonic()
        sampler = GpuMemorySampler()
        sampler.start()
        prompt_id = None
        try:
            prompt_id = self.client.submit(compiled.graph)
            manifest.tracking["comfy_prompt_id"] = prompt_id
            manifest.status = "running"
            save_manifest(manifest)
            history = self.client.wait(
                prompt_id,
                timeout_seconds=min(
                    int(recipe.resource_budget["timeout_seconds"]),
                    int(plan.envelope.budget.max_iteration_gpu_minutes * 60),
                ),
            )
            declarations = [MediaOutputDeclaration.model_validate(item) for item in compiled.outputs]
            discovered = discover_declared_outputs(history, declarations, local_path("output"))
            for item in discovered:
                add_output(
                    manifest,
                    item.path,
                    role=item.role,
                    media_kind=item.media_kind,
                    media=probe_media(item.path),
                )
            if not manifest.outputs:
                raise RuntimeExecutionError("ComfyUI completed without a declared output")
            for output in manifest.outputs:
                verify_sha256(Path(output.path), output.sha256)
            manifest.governance.validations["hashes"] = True
            manifest.status = "succeeded"
            elapsed_minutes = (time.monotonic() - started) / 60
            return GenerationResult(
                outputs=[Path(output.path) for output in manifest.outputs],
                run_id=manifest.run_id,
                actual_gpu_minutes=elapsed_minutes,
                usage={"elapsed_gpu_minutes": elapsed_minutes},
                request_hash=compiled.graph_sha256,
            )
        except Exception as exc:
            if prompt_id is not None and "Timed out" in str(exc):
                try:
                    self.client.cancel_owned_prompt(prompt_id)
                    manifest.tracking["deadline_cancellation"] = "requested_for_owned_prompt"
                except RuntimeExecutionError:
                    manifest.tracking["deadline_cancellation"] = "unconfirmed_inspect_comfy_queue"
            manifest.status = "failed"
            manifest.error = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            manifest.environment["gpu_memory"] = sampler.stop()
            save_manifest(manifest)
