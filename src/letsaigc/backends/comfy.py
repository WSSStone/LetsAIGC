from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import time
from pathlib import Path

import psutil
from PIL import Image

from ..assets.store import ArtifactStore
from ..comfy import ComfyClient
from ..config import load_catalog
from ..errors import ReadinessError, RuntimeExecutionError, ValidationError
from ..media import discover_declared_outputs, inspect_media_tools, probe_media
from ..models import ModelManager
from ..paths import find_repo_root, local_path
from ..policy import assert_model_allowed, verify_sha256
from ..schemas import GenerationPlan, MediaOutputDeclaration
from ..schemas.agent import MaskedGenerationPlan
from ..tracking.hardware import GpuMemorySampler
from ..tracking.manifest import add_output, create_manifest, save_manifest
from ..ui_analysis.inpaint import compose_inpaint, restore_to_canonical
from ..workflows.compiler import WorkflowCompiler, load_recipe
from .base import GenerationResult


class ComfyBackend:
    name = "comfy"

    def __init__(
        self,
        client: ComfyClient | None = None,
        compiler: WorkflowCompiler | None = None,
        artifact_store: ArtifactStore | None = None,
        trusted_sources: object | None = None,
    ) -> None:
        self.client = client or ComfyClient()
        self.compiler = compiler or WorkflowCompiler()
        self.artifact_store = artifact_store
        self.trusted_sources = trusted_sources

    def prepare(self, plan: GenerationPlan, *, iteration_id: str):
        """Validate and compile without submitting GPU work; usable by durable callers."""
        if plan.backend != "comfy" or not plan.recipe:
            raise ValidationError("Comfy backend requires a recipe-based plan")
        recipe = load_recipe(plan.recipe)
        masked = isinstance(plan, MaskedGenerationPlan)
        if not masked and len(plan.input_assets) > 1:
            raise ValidationError("Local Comfy recipes support at most one image input")
        no_edit_pixels = False
        if masked:
            if self.artifact_store is None:
                raise ValidationError("Masked Comfy execution requires the task artifact store")
            frozen = self.compiler.validate_masked_artifacts(
                plan, self.artifact_store, self.trusted_sources
            )
            no_edit_pixels = frozen["canonical_mask"].getbbox() is None
        required = recipe.resource_budget
        local_path().mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(local_path()).free < required["free_disk_gib"] * 1024**3:
            raise ReadinessError("Insufficient disk space for the approved recipe")
        if psutil.virtual_memory().total < required["ram_gib"] * 1024**3:
            raise ReadinessError("Insufficient system RAM for the approved recipe")
        catalog = load_catalog()
        lookup = catalog.by_id()
        model_hashes: dict[str, list[str]] = {}
        if not no_edit_pixels:
            manager = ModelManager(catalog=catalog)
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
        upload_subfolder = f"letsaigc-agent/{plan.session_id}/{plan.task_id}"
        uploaded: list[str] = []
        uploaded_mask: str | None = None
        if masked and not no_edit_pixels:
            image_ref = plan.image_mask.image_ref
            mask_ref = plan.image_mask.mask_ref
            for role, ref in (("image", image_ref), ("mask", mask_ref)):
                data = self.artifact_store.read(ref)
                staged = self.compiler.store.inputs_dir(plan.session_id) / f"{role}-{ref.sha256}.png"
                staged.write_bytes(data)
                handle = self.client.upload_image(staged, subfolder=upload_subfolder)
                if role == "image":
                    uploaded.append(handle)
                else:
                    uploaded_mask = handle
        else:
            for asset in plan.input_assets:
                verify_sha256(Path(asset.derived_path), asset.derived_sha256)
                uploaded.append(
                    self.client.upload_image(
                        Path(asset.derived_path),
                        subfolder=upload_subfolder,
                    )
                )
        if masked:
            compiled = self.compiler.compile(
                plan,
                uploaded_images=uploaded,
                uploaded_mask=uploaded_mask,
                trusted_artifacts=self.artifact_store,
                trusted_sources=self.trusted_sources,
                object_info=None if no_edit_pixels else self.client.object_info(),
            )
        else:
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
                **({
                    "masked_image_sha256": plan.image_mask.image_ref.sha256,
                    "masked_image_upload_sha256": plan.image_mask.image_ref.sha256,
                    "masked_mask_sha256": plan.image_mask.mask_ref.sha256,
                    "masked_mask_upload_sha256": plan.image_mask.mask_ref.sha256,
                    "masked_upload_handles": {
                        "image": uploaded[0] if uploaded else None,
                        "mask": uploaded_mask,
                    },
                } if masked else {}),
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
        return compiled, manifest, recipe

    def execute(self, plan: GenerationPlan, *, iteration_id: str) -> GenerationResult:
        compiled, manifest, recipe = self.prepare(plan, iteration_id=iteration_id)
        if compiled.validation.get("no_edit_pixels") is True:
            if not isinstance(plan, MaskedGenerationPlan) or self.artifact_store is None:
                raise RuntimeExecutionError("Masked no-op output requires the trusted artifact store")
            output_path = self._write_masked_noop(plan, manifest)
            manifest.status = "succeeded"
            save_manifest(manifest)
            return GenerationResult(
                outputs=[output_path],
                run_id=manifest.run_id,
                request_id=None,
                usage={"no_edit_pixels": True},
                request_hash=compiled.graph_sha256,
            )
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
            if isinstance(plan, MaskedGenerationPlan):
                self._complete_masked_output(plan, manifest, discovered)
            else:
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
                request_id=prompt_id,
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

    @staticmethod
    def _read_png_bytes(data: bytes, role: str) -> Image.Image:
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "PNG":
                    raise RuntimeExecutionError(f"Masked {role} is not a PNG")
                image.load()
                return image.copy()
        except RuntimeExecutionError:
            raise
        except (OSError, ValueError) as exc:
            raise RuntimeExecutionError(f"Masked {role} is not a readable PNG") from exc

    @staticmethod
    def _candidate_evidence(path: Path, data: bytes, artifact_ref) -> dict[str, object]:
        return {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "artifact_ref": artifact_ref.model_dump(mode="json"),
        }

    def _write_masked_noop(self, plan: MaskedGenerationPlan, manifest) -> Path:
        canonical = self._read_png_bytes(
            self.artifact_store.read(plan.image_mask.canonical_ref), "canonical image"
        )
        canonical_mask = self._read_png_bytes(
            self.artifact_store.read(plan.image_mask.canonical_edit_mask_ref), "canonical mask"
        )
        if canonical_mask.mode != "L" or canonical_mask.getbbox() is not None:
            raise RuntimeExecutionError("Masked no-op output does not have an empty canonical mask")
        result = compose_inpaint(canonical, None, canonical_mask)
        output_path = local_path("output", f"{manifest.run_id}-canonical.png")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result.save(output_path, format="PNG", optimize=False)
        add_output(manifest, output_path, role="primary_image", media_kind="image", media=probe_media(output_path))
        verify_sha256(output_path, manifest.outputs[-1].sha256)
        manifest.source["masked_noop"] = {
            "canonical_sha256": plan.image_mask.canonical_ref.sha256,
            "canonical_mask_sha256": plan.image_mask.canonical_edit_mask_ref.sha256,
            "output_sha256": manifest.outputs[-1].sha256,
        }
        manifest.tracking["no_edit_pixels"] = True
        manifest.governance.validations["no_edit_pixels"] = True
        return output_path

    def _complete_masked_output(self, plan: MaskedGenerationPlan, manifest, discovered) -> None:
        if len(discovered) != 1:
            raise RuntimeExecutionError("Masked Comfy execution must produce exactly one candidate image")
        if self.artifact_store is None:
            raise RuntimeExecutionError("Masked output evidence requires the trusted artifact store")
        candidate = discovered[0]
        candidate_path = Path(candidate.path)
        candidate_bytes = candidate_path.read_bytes()
        candidate_hash = hashlib.sha256(candidate_bytes).hexdigest()
        candidate_ref = self.artifact_store.put(
            plan.task_id,
            manifest.run_id,
            candidate_bytes,
            role="inpaint_candidate",
            media_type="image/png",
            source_ids=[plan.image_mask.image_ref.artifact_id, plan.image_mask.mask_ref.artifact_id],
        )
        manifest.source["masked_candidate"] = self._candidate_evidence(
            candidate_path, candidate_bytes, candidate_ref
        )
        manifest.source["masked_candidate"]["declared_role"] = candidate.role
        manifest.source["masked_candidate"]["declared_sha256"] = candidate_hash
        manifest.tracking["masked_candidate_sha256"] = candidate_hash
        manifest.tracking["masked_candidate_artifact_id"] = candidate_ref.artifact_id
        manifest.governance.validations["masked_candidate_hash"] = True
        save_manifest(manifest)

        candidate_image = self._read_png_bytes(candidate_bytes, "generated candidate")
        manifest.source["masked_candidate"].update(
            {
                "width": candidate_image.width,
                "height": candidate_image.height,
                "mode": candidate_image.mode,
            }
        )
        save_manifest(manifest)
        canonical = self._read_png_bytes(
            self.artifact_store.read(plan.image_mask.canonical_ref), "canonical image"
        )
        canonical_mask = self._read_png_bytes(
            self.artifact_store.read(plan.image_mask.canonical_edit_mask_ref), "canonical mask"
        )
        if canonical_mask.mode != "L":
            raise RuntimeExecutionError("Canonical edit mask must be grayscale")
        if candidate_image.size != (plan.image_mask.width, plan.image_mask.height):
            raise RuntimeExecutionError("Generated candidate dimensions do not match the prepared binding")
        if candidate_image.mode != canonical.mode:
            raise RuntimeExecutionError("Generated candidate mode does not match the canonical image")
        result = restore_to_canonical(canonical, candidate_image, canonical_mask, plan.image_mask)
        output_path = local_path("output", f"{manifest.run_id}-canonical.png")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result.save(output_path, format="PNG", optimize=False)
        add_output(
            manifest,
            output_path,
            role="primary_image",
            media_kind="image",
            media=probe_media(output_path),
            derived_from_sha256=candidate_hash,
        )
        verify_sha256(output_path, manifest.outputs[-1].sha256)
        manifest.source["masked_composite"] = {
            "canonical_sha256": plan.image_mask.canonical_ref.sha256,
            "canonical_mask_sha256": plan.image_mask.canonical_edit_mask_ref.sha256,
            "candidate_sha256": candidate_hash,
            "output_sha256": manifest.outputs[-1].sha256,
        }
