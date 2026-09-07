"""Coordinator adapter for one approved UI masked Comfy operation.

The adapter is deliberately thin around :class:`ComfyBackend`: compilation,
uploads and the Comfy prompt receipt remain owned by the existing native
backend.  This module supplies the UI child boundary around that backend.  In
particular, it reloads the immutable child request for every provider-facing
call, keeps the original operation ID for recovery, and performs the final
canonical composite on CPU.
"""

from __future__ import annotations

import io
import json
import time
import uuid
from collections.abc import Mapping
from typing import Any

from PIL import Image

from ..backends.comfy import ComfyBackend
from ..comfy.client import PINNED_COMFYUI_VERSION, ComfyClient
from ..config import load_catalog, load_workflow_contract
from ..errors import RuntimeExecutionError
from ..media import discover_declared_outputs
from ..models import ModelManager
from ..paths import find_repo_root, local_path
from ..policy import verify_sha256
from ..schemas.agent import MaskedGenerationPlan
from ..schemas.pipeline import ArtifactRef, Cost, PipelinePlan, digest
from ..schemas.ui import UIAnalysisRequest, UIObservation, UIStepBinding
from ..schemas.ui_provider import UIInputManifest
from ..tracking.manifest import save_manifest
from ..ui_analysis.inpaint import restore_to_canonical
from ..ui_analysis.selection import TrustedSource
from ..workflows.compiler import load_recipe, normalize_model_paths
from .comfy import ComfyOperationBackend, ComfyReceipt
from .contracts import Capability, Submission
from .errors import OutcomeUnknown, PipelineError

NATIVE_INPAINT_DEPENDENCIES = (
    "configs/workflows/recipes/sdxl-inpaint.yaml",
    "workflows/ui/sdxl-inpaint.json",
    "workflows/api/sdxl-inpaint.json",
    "workflows/contracts/sdxl-inpaint.yaml",
    "configs/models/catalog.yaml",
    "configs/runtime/comfyui.lock.yaml",
)
NATIVE_INPAINT_NODES = (
    "CheckpointLoaderSimple",
    "LoadImage",
    "ImageToMask",
    "VAEEncodeForInpaint",
    "CLIPTextEncode",
    "KSampler",
    "VAEDecode",
    "SaveImage",
)


def inpaint_readiness(client: ComfyClient | None = None) -> dict[str, Any]:
    """Report T028 readiness using only local files and read-only Comfy calls."""
    result: dict[str, Any] = {
        "ready": False,
        "native_dependencies": False,
        "catalog_model": False,
        "model_files": False,
        "endpoint": False,
        "cuda": False,
        "object_info": False,
        "reasons": [],
    }
    root = find_repo_root().resolve()
    def add_reason(code: str) -> None:
        if code not in result["reasons"]:
            result["reasons"].append(code)

    try:
        if not all((root / path).is_file() for path in NATIVE_INPAINT_DEPENDENCIES):
            raise ValueError("native inpaint dependency is missing")
        recipe = load_recipe("sdxl-inpaint")
        contract = load_workflow_contract("sdxl-inpaint")
        catalog = load_catalog()
        model = catalog.by_id().get("sdxl-base-1.0")
        if model is None or recipe.models != [model.id] or contract.models != recipe.models:
            raise ValueError("native inpaint recipe, contract, and catalog disagree")
        result["native_dependencies"] = True
        result["catalog_model"] = True
        model_results = ModelManager(catalog=catalog).verify_model(model)
        result["model_files"] = bool(model_results) and all(item.get("ok") is True for item in model_results)
        if not result["model_files"]:
            add_reason("model_files_unverified")
    except Exception:
        add_reason("native_contract_unavailable")

    try:
        probe = client or ComfyClient(timeout=2.0)
    except Exception:
        probe = None
        add_reason("comfy_client_unavailable")
    if probe is not None:
        try:
            stats = probe.system_stats()
            object_info = probe.object_info()
            result["endpoint"] = True
        except Exception:
            add_reason("comfy_probe_failed")
        else:
            if not isinstance(stats, dict) or not isinstance(object_info, dict):
                add_reason("comfy_response_malformed")
                stats = None
                object_info = None
            if stats is not None and object_info is not None:
                system = stats.get("system")
                devices = stats.get("devices")
                if not isinstance(system, dict) or system.get("comfyui_version") != PINNED_COMFYUI_VERSION:
                    add_reason("comfy_version_unpinned")
                cuda = (
                    [item for item in devices if isinstance(item, dict) and item.get("type") == "cuda"]
                    if isinstance(devices, list)
                    else []
                )
                result["cuda"] = bool(cuda) and all(
                    type(item.get("torch_vram_total")) is int
                    and type(item.get("torch_vram_free")) is int
                    and 0 <= item["torch_vram_free"] <= item["torch_vram_total"]
                    for item in cuda
                )
                result["object_info"] = all(
                    node in object_info
                    and isinstance(object_info[node], dict)
                    and "custom_nodes" not in str(object_info[node].get("python_module", "")).replace("\\", "/")
                    for node in NATIVE_INPAINT_NODES
                )
                if not result["cuda"]:
                    add_reason("cuda_unobservable")
                if not result["object_info"]:
                    add_reason("object_info_incomplete")
    result["ready"] = not result["reasons"] and all(
        result[key]
        for key in ("native_dependencies", "catalog_model", "model_files", "endpoint", "cuda", "object_info")
    )
    return result


class UIInpaintOperationBackend:
    """Run exactly one approved ``ui.inpaint`` child operation.

    ``service`` is the preferred constructor because it supplies the ledger
    and task-scoped :class:`ArtifactStore` together.  Tests and narrowly
    scoped callers may pass ``ledger=`` and ``artifacts=`` instead.  ``backend``
    is an existing native :class:`ComfyBackend`; passing a
    :class:`ComfyOperationBackend` is also supported for dependency injection.
    """

    capability = Capability(id="ui.inpaint", can_cancel=True, resource="local-gpu")
    native_dependency_paths = NATIVE_INPAINT_DEPENDENCIES

    def __init__(
        self,
        service: Any | None = None,
        backend: ComfyBackend | ComfyOperationBackend | None = None,
        *,
        ledger: Any | None = None,
        artifacts: Any | None = None,
    ) -> None:
        # Also accept the compact positional form
        # ``UIInpaintOperationBackend(ledger, artifacts)`` for worker setup.
        if (
            service is not None
            and not hasattr(service, "ledger")
            and backend is not None
            and hasattr(backend, "read")
            and ledger is None
            and artifacts is None
        ):
            ledger, artifacts, service, backend = service, backend, None, None
        if service is not None:
            if ledger is not None or artifacts is not None:
                raise TypeError("Pass service or ledger/artifacts, not both")
            ledger = service.ledger
            artifacts = service.artifacts
        if ledger is None or artifacts is None:
            raise TypeError("UIInpaintOperationBackend requires service or ledger and artifacts")
        self.ledger = ledger
        self.artifacts = artifacts

        if isinstance(backend, ComfyOperationBackend):
            self._scheduler = backend
            self.backend = backend.backend
        else:
            self.backend = backend or ComfyBackend(artifact_store=artifacts)
            if getattr(self.backend, "artifact_store", None) is None:
                # A native backend without a store cannot validate the frozen
                # image/mask pair.  Supplying the coordinator's store is safe
                # because the store is task scoped and content addressed.
                self.backend.artifact_store = artifacts
            try:
                self._scheduler = ComfyOperationBackend(self.backend)
            except AttributeError:
                # Small offline fakes need not expose a URL.  They still use
                # the same scheduler methods and never reach a real endpoint.
                self._scheduler = object.__new__(ComfyOperationBackend)
                self._scheduler.backend = self.backend
        configured = getattr(self.backend, "trusted_sources", None)
        self._configured_trusted_sources = (
            dict(configured) if isinstance(configured, Mapping) and configured else None
        )

    # ------------------------------------------------------------------
    # Frozen child/request validation
    # ------------------------------------------------------------------
    def _context(
        self, operation_id: str, supplied: UIStepBinding | dict[str, Any] | None = None
    ) -> tuple[Any, PipelinePlan, UIStepBinding, MaskedGenerationPlan]:
        operation = self.ledger.get(operation_id)
        plan = self.ledger.plan(operation.task_id)
        if plan.workflow_type != "ui_inpaint" or plan.envelope.allowed_capabilities != [self.capability.id]:
            raise PipelineError("prohibited_capability", "Operation is not an approved UI inpaint child")
        frozen = UIStepBinding.model_validate(self.ledger.ui_binding(operation_id).model_dump(mode="json"))
        if supplied is not None:
            candidate = UIStepBinding.model_validate(
                supplied.model_dump(mode="json") if isinstance(supplied, UIStepBinding) else supplied
            )
            if candidate != frozen:
                raise PipelineError("input_changed", "UI inpaint submission binding changed")

        try:
            request_ref = ArtifactRef.model_validate(plan.parameters["request_ref"])
        except Exception:
            raise PipelineError("input_changed", "UI inpaint child request reference is unavailable") from None
        if request_ref.task_id != operation.task_id or request_ref.role != "request" or request_ref not in plan.inputs:
            raise PipelineError("input_changed", "UI inpaint child request is outside the approved child")
        try:
            request = MaskedGenerationPlan.model_validate_json(self.artifacts.read(request_ref))
        except Exception:
            raise PipelineError("input_changed", "UI inpaint child request is unavailable or changed") from None
        if request.task_id != operation.task_id or request.envelope.budget != plan.envelope.budget:
            raise PipelineError("input_changed", "UI inpaint request differs from its approved child")
        mask = request.image_mask
        if (
            mask.selection_ref != frozen.selection_ref
            or mask.selection_revision != frozen.selection_revision
            or mask.selection_hash != frozen.selection_hash
        ):
            raise PipelineError("selection_conflict", "UI inpaint selection differs from the approved child")

        # Reading all six refs here makes every provider-facing operation
        # fail closed if immutable content has disappeared or changed.
        for ref in (
            mask.image_ref,
            mask.mask_ref,
            mask.canonical_ref,
            mask.canonical_edit_mask_ref,
            mask.selection_ref,
            mask.view_transform_ref,
        ):
            if ref.task_id != operation.task_id:
                raise PipelineError("artifact_scope", "UI inpaint input is outside the child scope")
            self.artifacts.read(ref)
        return operation, plan, frozen, request

    @staticmethod
    def _read_png(data: bytes, label: str) -> Image.Image:
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "PNG":
                    raise RuntimeExecutionError(f"UI inpaint {label} must be a PNG")
                image.load()
                return image.copy()
        except RuntimeExecutionError:
            raise
        except (OSError, ValueError) as exc:
            raise RuntimeExecutionError(f"UI inpaint {label} is not a readable PNG") from exc

    def _validate_image_mask(
        self, request: MaskedGenerationPlan
    ) -> tuple[Image.Image, Image.Image, Image.Image, Image.Image]:
        """Validate the exact prepared pair and the canonical restoration pair."""

        mask = request.image_mask
        prepared = self._read_png(self.artifacts.read(mask.image_ref), "prepared image")
        prepared_mask = self._read_png(self.artifacts.read(mask.mask_ref), "prepared mask")
        canonical = self._read_png(self.artifacts.read(mask.canonical_ref), "canonical image")
        canonical_mask = self._read_png(
            self.artifacts.read(mask.canonical_edit_mask_ref), "canonical edit mask"
        )
        if prepared.size != (mask.width, mask.height):
            raise RuntimeExecutionError("Prepared image dimensions do not match the frozen binding")
        if prepared_mask.mode != "L" or prepared_mask.size != prepared.size:
            raise RuntimeExecutionError("Prepared image/mask pair does not match the frozen binding")
        if canonical.size != (mask.canonical_width, mask.canonical_height):
            raise RuntimeExecutionError("Canonical image dimensions do not match the frozen binding")
        if canonical_mask.mode != "L" or canonical_mask.size != canonical.size:
            raise RuntimeExecutionError("Canonical edit mask does not match the canonical image")
        return prepared, prepared_mask, canonical, canonical_mask

    def _verify_dependencies(self, plan: PipelinePlan, request: MaskedGenerationPlan) -> dict[str, str]:
        """Verify the exact native files frozen by child planning.

        PipelineService verifies these hashes at the plan boundary.  The
        backend repeats the check immediately before native preparation so a
        long approval interval cannot turn a changed compiler or recipe into
        an unreviewed provider request.
        """
        declared = dict(plan.dependency_hashes)
        if not declared or any(path not in declared for path in self.native_dependency_paths):
            raise PipelineError("dependency_changed", "UI inpaint native dependencies are not frozen")
        if request.dependency_hashes and dict(request.dependency_hashes) != declared:
            raise PipelineError("dependency_changed", "UI inpaint request dependencies differ from its child plan")
        root = find_repo_root().resolve()
        for name in self.native_dependency_paths:
            path = (root / name).resolve()
            if not path.is_file() or not path.is_relative_to(root):
                raise PipelineError("dependency_changed", "A frozen UI inpaint dependency is unavailable")
            try:
                verify_sha256(path, declared[name])
            except Exception as exc:
                raise PipelineError("dependency_changed", "A frozen UI inpaint dependency changed") from exc
        return declared

    def _root_originals(self, plan: PipelinePlan) -> dict[str, ArtifactRef]:
        """Resolve approved root originals by immutable content hash."""
        root_task_id = str(plan.parameters.get("root_task_id") or plan.task_id)
        try:
            root_plan = self.ledger.plan(root_task_id)
        except Exception as exc:
            raise PipelineError("source_scope", "UI inpaint root plan is unavailable") from exc
        refs: dict[str, ArtifactRef] = {
            ref.sha256: ref for ref in root_plan.inputs if ref.role == "original" and ref.task_id == root_task_id
        }
        root_request = None
        if "request_ref" in root_plan.parameters:
            try:
                root_request_ref = ArtifactRef.model_validate(root_plan.parameters["request_ref"])
                root_request = UIAnalysisRequest.model_validate_json(self.artifacts.read(root_request_ref))
            except Exception as exc:
                raise PipelineError("source_scope", "Root input request is unavailable") from exc
        if root_request is not None:
            if root_request.input.kind == "manual":
                candidates = list(root_request.input.inputs)
                if root_request.input.metadata_ref is not None:
                    try:
                        manifest = UIInputManifest.model_validate_json(
                            self.artifacts.read(root_request.input.metadata_ref)
                        )
                        candidates.extend(source.original_ref for source in manifest.sources)
                    except Exception as exc:
                        raise PipelineError("source_scope", "Root input manifest is unavailable") from exc
            else:
                candidates = []
                for operation in self.ledger.list_operations(root_task_id):
                    if operation.state != "succeeded":
                        continue
                    for raw in operation.result.get("artifacts", []):
                        try:
                            ref = ArtifactRef.model_validate(raw)
                            if ref.role == "input_manifest":
                                manifest = UIInputManifest.model_validate_json(self.artifacts.read(ref))
                                candidates.extend(source.original_ref for source in manifest.sources)
                        except Exception:
                            continue
            for ref in candidates:
                if ref.role == "original" and ref.task_id == root_task_id:
                    self.artifacts.read(ref)
                    refs.setdefault(ref.sha256, ref)
        return refs

    def _trusted_sources(
        self, plan: PipelinePlan, request: MaskedGenerationPlan
    ) -> dict[str, TrustedSource]:
        """Build child-scoped trusted bindings from root originals and rebinding."""
        if self._configured_trusted_sources is not None and self._trusted_source_map_matches(
            self._configured_trusted_sources, request
        ):
            return dict(self._configured_trusted_sources)
        try:
            selection = json.loads(self.artifacts.read(request.image_mask.selection_ref).decode("utf-8"))
        except Exception as exc:
            raise PipelineError("input_changed", "UI inpaint selection is unavailable") from exc
        if not isinstance(selection, dict) or not isinstance(selection.get("sources"), list):
            raise PipelineError("input_changed", "UI inpaint selection is malformed")
        root_originals = self._root_originals(plan)
        canonical = self._read_png(self.artifacts.read(request.image_mask.canonical_ref), "canonical image")
        trusted: dict[str, TrustedSource] = {}
        for raw in selection["sources"]:
            if not isinstance(raw, dict) or not isinstance(raw.get("source_id"), str):
                raise PipelineError("source_scope", "UI inpaint selection source is malformed")
            source_id = raw["source_id"]
            original = root_originals.get(raw.get("original_sha256"))
            if original is None:
                raise PipelineError("source_scope", "UI inpaint source is outside approved root inputs")
            local_original = original
            if original.task_id != plan.task_id:
                local_original = self.artifacts.put(
                    plan.task_id,
                    "trusted-source",
                    self.artifacts.read(original),
                    role="original",
                    media_type=original.media_type,
                    source_ids=[original.artifact_id],
                )
            layout_ref = None
            if raw.get("layout_ref") is not None:
                try:
                    layout_ref = ArtifactRef.model_validate(raw["layout_ref"])
                except Exception as exc:
                    raise PipelineError("source_scope", "UI inpaint layout reference is malformed") from exc
                if layout_ref.task_id != plan.task_id:
                    raise PipelineError("source_scope", "UI inpaint layout is outside the child scope")
                self.artifacts.read(layout_ref)
            trusted[source_id] = TrustedSource(
                source_id,
                local_original,
                canonical.width,
                canonical.height,
                layout_ref,
                request.image_mask.canonical_ref,
            )
        if len(trusted) != 1:
            raise PipelineError("source_scope", "UI inpaint requires one trusted source")
        self.backend.trusted_sources = trusted
        return trusted

    def _trusted_source_map_matches(
        self, sources: Mapping[str, Any], request: MaskedGenerationPlan
    ) -> bool:
        """Prevent a configured map from leaking across reused child tasks."""
        try:
            raw = json.loads(self.artifacts.read(request.image_mask.selection_ref).decode("utf-8"))
            selected = raw["sources"]
        except Exception:
            return False
        if not isinstance(selected, list) or set(sources) != {
            item.get("source_id") for item in selected if isinstance(item, dict)
        }:
            return False
        for item in selected:
            if not isinstance(item, dict):
                return False
            trusted = sources.get(item.get("source_id"))
            if not isinstance(trusted, TrustedSource):
                return False
            if trusted.canonical_ref != request.image_mask.canonical_ref:
                return False
            if trusted.original_sha256 != item.get("original_sha256"):
                return False
            layout = item.get("layout_ref")
            if layout is None:
                if trusted.layout_ref is not None:
                    return False
            else:
                try:
                    if trusted.layout_ref != ArtifactRef.model_validate(layout):
                        return False
                except Exception:
                    return False
        return True

    def _validate_native_masked_plan(
        self, plan: PipelinePlan, request: MaskedGenerationPlan
    ) -> dict[str, Any]:
        trusted = self._trusted_sources(plan, request)
        compiler = getattr(self.backend, "compiler", None)
        validate = getattr(compiler, "validate_masked_artifacts", None)
        if not callable(validate):
            raise PipelineError("dependency_changed", "Native Comfy compiler is unavailable")
        try:
            frozen = validate(request, self.artifacts, trusted)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError("input_changed", "Native masked input validation failed") from exc
        return {"trusted_sources": trusted, "frozen": frozen}

    def _validate_native_object_info(self, request: MaskedGenerationPlan, object_info: dict[str, Any]) -> None:
        """Run the existing native node validator with inert upload handles."""
        compiler = self.backend.compiler
        recipe = compiler._validate_masked_plan_metadata(request)
        base_path = (find_repo_root() / recipe.base_workflow).resolve()
        graph = compiler._compile_sdxl_inpaint(
            json.loads(base_path.read_text(encoding="utf-8")),
            "preflight/image.png",
            "preflight/mask.png",
        )
        compiler._apply_parameters(graph, recipe, request.parameters)
        compiler._scope_outputs(graph, request)
        normalize_model_paths(graph, object_info)
        compiler._validate_nodes(graph, recipe, object_info)

    def _provider_readiness(self, request: MaskedGenerationPlan) -> None:
        """Perform bounded read-only native checks before any upload/submit."""
        client = getattr(self.backend, "client", None)
        stats_call = getattr(client, "system_stats", None)
        object_info_call = getattr(client, "object_info", None)
        if not callable(stats_call) or not callable(object_info_call):
            raise PipelineError("model_not_ready", "ComfyUI readiness probes are unavailable")
        try:
            stats = stats_call()
            object_info = object_info_call()
        except Exception as exc:
            raise PipelineError("model_not_ready", "ComfyUI readiness probes failed") from exc
        if not isinstance(stats, dict) or not isinstance(object_info, dict):
            raise PipelineError("model_not_ready", "ComfyUI readiness probes returned malformed data")
        system = stats.get("system")
        devices = stats.get("devices")
        if not isinstance(system, dict) or system.get("comfyui_version") != PINNED_COMFYUI_VERSION:
            raise PipelineError("model_not_ready", "ComfyUI is not the pinned native version")
        cuda_devices = (
            [device for device in devices if isinstance(device, dict) and device.get("type") == "cuda"]
            if isinstance(devices, list)
            else []
        )
        if not cuda_devices:
            raise PipelineError("model_not_ready", "UI inpaint requires an observable CUDA device")
        for device in cuda_devices:
            total = device.get("torch_vram_total")
            free = device.get("torch_vram_free")
            if type(total) is not int or type(free) is not int or total < 0 or free < 0 or free > total:
                raise PipelineError("model_not_ready", "ComfyUI CUDA counters are malformed")
        try:
            self._validate_native_object_info(request, object_info)
        except Exception as exc:
            raise PipelineError("model_not_ready", "ComfyUI native nodes do not satisfy the inpaint contract") from exc

    def preflight(self, plan: PipelinePlan) -> dict[str, Any]:
        """Validate a frozen child locally and probe native readiness read-only.

        Planning and approval code uses this hook to verify the immutable
        request, native compiler inputs and image/mask pair.  The only native
        calls are read-only system/object-info probes; uploads and submission
        remain at the post-approval preparation boundary.
        """

        try:
            child = PipelinePlan.model_validate(plan.model_dump(mode="json"))
            request_ref = ArtifactRef.model_validate(child.parameters["request_ref"])
            selection_ref = ArtifactRef.model_validate(child.parameters["selection_ref"])
        except Exception:
            raise PipelineError("input_changed", "UI inpaint child binding is invalid") from None
        if (
            child.workflow_type != "ui_inpaint"
            or child.envelope.allowed_capabilities != [self.capability.id]
            or request_ref.role != "request"
            or request_ref.task_id != child.task_id
            or request_ref not in child.inputs
        ):
            raise PipelineError("prohibited_capability", "Plan is not an approved UI inpaint child")
        try:
            request = MaskedGenerationPlan.model_validate_json(self.artifacts.read(request_ref))
        except Exception:
            raise PipelineError("input_changed", "UI inpaint request is unavailable or changed") from None
        if request.task_id != child.task_id or request.envelope.budget != child.envelope.budget:
            raise PipelineError("input_changed", "UI inpaint request differs from its approved child")
        if request.image_mask.selection_ref != selection_ref:
            raise PipelineError("selection_conflict", "UI inpaint selection differs from its child binding")
        self._verify_dependencies(child, request)
        self._validate_native_masked_plan(child, request)
        _prepared, prepared_mask, canonical, canonical_mask = self._validate_image_mask(request)
        if canonical_mask.getbbox() is not None and child.envelope.budget.max_iteration_gpu_minutes <= 0:
            raise PipelineError("budget_scope", "A non-empty inpaint requires a positive GPU iteration budget")
        if canonical_mask.getbbox() is not None:
            self._provider_readiness(request)
        return {
            "ready": True,
            "task_id": child.task_id,
            "request_ref": request_ref.model_dump(mode="json"),
            "image_ref": request.image_mask.image_ref.model_dump(mode="json"),
            "mask_ref": request.image_mask.mask_ref.model_dump(mode="json"),
            "prepared_size": list(prepared_mask.size),
            "canonical_size": list(canonical.size),
            "no_edit_pixels": canonical_mask.getbbox() is None,
            "dependencies": dict(child.dependency_hashes),
        }

    # Runtime configuration code uses the explicit child name when probing a
    # capability; retaining the alias keeps the provider-free hook discoverable.
    preflight_child = preflight

    # ------------------------------------------------------------------
    # Provider scheduler boundary
    # ------------------------------------------------------------------
    def prepare(self, operation_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Compile and stage the exact child request without submitting it."""

        supplied = UIStepBinding.model_validate(arguments["binding"])
        _operation, plan, _frozen, request = self._context(operation_id, supplied)
        self._verify_dependencies(plan, request)
        self._validate_native_masked_plan(plan, request)
        _prepared, _prepared_mask, _canonical, canonical_mask = self._validate_image_mask(request)
        if canonical_mask.getbbox() is not None and request.envelope.budget.max_iteration_gpu_minutes <= 0:
            raise PipelineError("budget_scope", "A non-empty inpaint requires a positive GPU iteration budget")
        if canonical_mask.getbbox() is not None:
            self._provider_readiness(request)
        compiled, manifest, recipe = self.backend.prepare(request, iteration_id=operation_id)
        no_edit_pixels = compiled.validation.get("no_edit_pixels") is True
        metadata = {
            "run_id": manifest.run_id,
            "task_id": request.task_id,
            "operation_id": operation_id,
            "started_at": time.time(),
            # ComfyReceipt keeps a positive transport timeout even for a
            # deterministic zero-mask no-op; no provider request is emitted
            # and inspect() reports zero actual GPU cost for that path.
            "timeout_seconds": (
                1.0
                if no_edit_pixels
                else min(
                    recipe.resource_budget["timeout_seconds"],
                    request.envelope.budget.max_iteration_gpu_minutes * 60,
                )
            ),
            "outputs": compiled.outputs,
            "graph_sha256": compiled.graph_sha256,
        }
        ComfyReceipt.model_validate(metadata)
        source = getattr(manifest, "source", {})
        if (
            not isinstance(source, dict)
            or source.get("masked_image_sha256") != request.image_mask.image_ref.sha256
            or source.get("masked_mask_sha256") != request.image_mask.mask_ref.sha256
        ):
            raise PipelineError(
                "input_changed",
                "Compiled Comfy input pair does not match the approved image and mask",
            )
        manifest.parent_run_id = "pipeline-" + digest(request.task_id)[:32]
        manifest.tracking["pipeline_operation_id"] = operation_id
        save_manifest(manifest)
        return {
            "graph": compiled.graph,
            "receipt": metadata,
            "no_edit_pixels": no_edit_pixels,
            "masked_plan_sha256": digest(request),
        }

    def _validate_prepared(
        self, operation_id: str, request: MaskedGenerationPlan, prepared: dict[str, Any]
    ) -> ComfyReceipt:
        if not isinstance(prepared.get("graph"), dict) or not isinstance(prepared.get("receipt"), dict):
            raise PipelineError("input_changed", "UI inpaint preparation is invalid")
        receipt = ComfyReceipt.model_validate(prepared["receipt"])
        if receipt.operation_id != operation_id or receipt.task_id != request.task_id:
            raise PipelineError("input_changed", "UI inpaint preparation belongs to another operation")
        if prepared.get("masked_plan_sha256") != digest(request):
            raise PipelineError("input_changed", "UI inpaint preparation does not match the frozen request")
        _prepared, _prepared_mask, _canonical, canonical_mask = self._validate_image_mask(request)
        no_edit = canonical_mask.getbbox() is None
        if prepared.get("no_edit_pixels") is not no_edit:
            raise PipelineError("input_changed", "UI inpaint no-op status changed")
        graph_sha256 = digest(prepared["graph"])
        if graph_sha256 != receipt.graph_sha256:
            raise PipelineError("input_changed", "UI inpaint prepared graph hash does not match its receipt")
        try:
            manifest = self._scheduler.checked_manifest(receipt.model_dump(mode="json"))
        except Exception as exc:
            raise PipelineError("receipt_mismatch", "UI inpaint preparation manifest is unavailable") from exc
        source = getattr(manifest, "source", {})
        if (
            not isinstance(source, dict)
            or source.get("masked_image_sha256") != request.image_mask.image_ref.sha256
            or source.get("masked_mask_sha256") != request.image_mask.mask_ref.sha256
        ):
            raise PipelineError("input_changed", "Prepared Comfy inputs differ from the approved pair")
        return receipt

    @staticmethod
    def _receipt_metadata(operation: Any) -> dict[str, Any]:
        receipt = operation.result.get("receipt", {})
        if not isinstance(receipt, dict):
            raise PipelineError("receipt_mismatch", "Provider receipt is unavailable")
        parsed = ComfyReceipt.model_validate(receipt)
        if parsed.operation_id != operation.operation_id or parsed.task_id != operation.task_id:
            raise PipelineError("receipt_mismatch", "Provider receipt does not belong to this operation")
        return parsed.model_dump(mode="json")

    def submit(self, operation_id: str, arguments: dict[str, Any]) -> Submission:
        """Submit once after the ledger has crossed its submitting boundary."""

        operation, plan, _frozen, request = self._context(operation_id, arguments.get("binding"))
        self._verify_dependencies(plan, request)
        if operation.provider_request_id:
            metadata = self._receipt_metadata(operation)
            return Submission(request_id=operation.provider_request_id, metadata=metadata)
        if operation.state == "outcome_unknown":
            # A lost acceptance is read-only recoverable.  Retrying POST here
            # would create a second generation for the same approved child.
            raise OutcomeUnknown()
        if operation.state != "submitting":
            raise PipelineError("operation_state", "UI inpaint must be submitted from the ledger boundary")

        prepared = arguments.get("prepared")
        if prepared is None:
            prepared = self.prepare(operation_id, {"binding": arguments["binding"]})
        prepared_receipt = self._validate_prepared(operation_id, request, prepared)
        # No-edit pixels are a deterministic CPU result and must never enter
        # Comfy's queue.
        if prepared.get("no_edit_pixels") is True:
            return Submission(
                request_id="noop-" + operation_id.removeprefix("op-")[:48],
                metadata={**prepared_receipt.model_dump(mode="json"), "no_edit_pixels": True},
            )
        return self._scheduler.submit(
            operation_id,
            {"prepared": prepared},
        )

    def recover(self, operation_id: str) -> Submission | None:
        """Read the original operation receipt; never submit during recovery."""

        operation, _plan, _binding, _request = self._context(operation_id)
        if operation.provider_request_id:
            return Submission(
                request_id=operation.provider_request_id,
                metadata=self._receipt_metadata(operation),
            )
        if operation.state not in {"submitting", "outcome_unknown"}:
            return None
        receipt = self._scheduler.recover(operation_id)
        if receipt is None:
            return None
        parsed = ComfyReceipt.model_validate(receipt.metadata)
        if parsed.operation_id != operation_id or parsed.task_id != operation.task_id:
            raise PipelineError("receipt_mismatch", "Recovered provider receipt belongs to another operation")
        return Submission(request_id=receipt.request_id, metadata=parsed.model_dump(mode="json"))

    def _validated_submission(
        self, submission: Submission
    ) -> tuple[Any, PipelinePlan, MaskedGenerationPlan, dict[str, Any]]:
        try:
            metadata = ComfyReceipt.model_validate(submission.metadata)
        except Exception:
            # Local no-op submissions carry a Comfy receipt plus a marker.
            metadata = ComfyReceipt.model_validate(
                {key: value for key, value in submission.metadata.items() if key != "no_edit_pixels"}
            )
        operation, plan, _binding, request = self._context(metadata.operation_id)
        if operation.provider_request_id and operation.provider_request_id != submission.request_id:
            raise PipelineError("operation_scope", "Provider request is not owned by this operation")
        if metadata.task_id != operation.task_id:
            raise PipelineError("operation_scope", "Provider receipt task does not match the child")
        _prepared, _prepared_mask, _canonical, canonical_mask = self._validate_image_mask(request)
        actual_noop = canonical_mask.getbbox() is None
        supplied_noop = submission.metadata.get("no_edit_pixels") is True
        if supplied_noop != actual_noop:
            raise PipelineError("input_changed", "UI inpaint no-op status does not match the frozen mask")
        validated = metadata.model_dump(mode="json")
        if actual_noop:
            validated["no_edit_pixels"] = True
        return operation, plan, request, validated

    def _history(
        self, submission: Submission
    ) -> tuple[Any, PipelinePlan, MaskedGenerationPlan, dict[str, Any], dict[str, Any]]:
        operation, plan, request, metadata = self._validated_submission(submission)
        if metadata.get("no_edit_pixels") or submission.metadata.get("no_edit_pixels"):
            return operation, plan, request, metadata, {}
        try:
            uuid.UUID(submission.request_id)
        except (ValueError, AttributeError):
            # Provider IDs are UUIDs in Comfy, but deterministic fakes use a
            # constrained identifier.  The client still scopes the query.
            if not submission.request_id or len(submission.request_id) > 128:
                raise PipelineError("operation_scope") from None
        history = self.backend.client.history(submission.request_id)
        record = history.get(submission.request_id) if isinstance(history, dict) else None
        return operation, plan, request, metadata, record

    def inspect(self, submission: Submission) -> UIObservation:
        operation, _plan, _request, metadata, record = self._history(submission)
        if metadata.get("no_edit_pixels") or submission.metadata.get("no_edit_pixels"):
            return UIObservation(state="succeeded", actual=Cost())
        elapsed = max(0.0, time.time() - metadata["started_at"])
        if record is not None:
            status = record.get("status", {}) if isinstance(record, dict) else {}
            if status.get("status_str") == "error":
                return UIObservation(state="failed", actual=self._duration(status, elapsed))
            if status.get("completed") is True or record.get("outputs"):
                return UIObservation(state="succeeded", actual=self._duration(status, elapsed))
            if elapsed > metadata["timeout_seconds"]:
                return UIObservation(state="unknown", actual=None)
            return UIObservation(state="running", actual=None)
        queue = self.backend.client.queue()
        queued = self._queue_ids(queue)
        if submission.request_id in queued and elapsed <= metadata["timeout_seconds"]:
            return UIObservation(state="running", actual=None)
        return UIObservation(state="unknown", actual=None)

    @staticmethod
    def _duration(status: dict[str, Any], fallback: float) -> Cost:
        messages = dict(status.get("messages", [])) if isinstance(status.get("messages", []), list) else {}
        start = messages.get("execution_start", {}).get("timestamp")
        end = (
            messages.get("execution_success")
            or messages.get("execution_error")
            or messages.get("execution_interrupted")
            or {}
        ).get("timestamp")
        seconds = max(0.0, (end - start) / 1000) if start is not None and end is not None else fallback
        return Cost(gpu_minutes=seconds / 60)

    @staticmethod
    def _queue_ids(queue: dict[str, Any]) -> set[str]:
        ids: set[str] = set()
        for name in ("queue_running", "queue_pending"):
            for item in queue.get(name, []) if isinstance(queue, dict) else []:
                if isinstance(item, (list, tuple)) and len(item) > 1:
                    ids.add(str(item[1]))
                elif isinstance(item, dict) and item.get("prompt_id"):
                    ids.add(str(item["prompt_id"]))
        return ids

    def collect(self, submission: Submission) -> list[tuple[str, bytes, str]]:
        operation, _plan, request, metadata, record = self._history(submission)
        _prepared, _prepared_mask, canonical, canonical_mask = self._validate_image_mask(request)
        if metadata.get("no_edit_pixels") or submission.metadata.get("no_edit_pixels"):
            output = self._png(canonical)
            self.artifacts.put(
                operation.task_id,
                operation.operation_id,
                output,
                role="reconstruction",
                media_type="image/png",
                source_ids=[
                    request.image_mask.canonical_ref.artifact_id,
                    request.image_mask.canonical_edit_mask_ref.artifact_id,
                ],
            )
            return [("reconstruction", output, "image/png")]
        if not isinstance(record, dict):
            raise OutcomeUnknown()
        manifest = self._scheduler.checked_manifest(metadata)
        source = getattr(manifest, "source", {})
        if (
            not isinstance(source, dict)
            or source.get("masked_image_sha256") != request.image_mask.image_ref.sha256
            or source.get("masked_mask_sha256") != request.image_mask.mask_ref.sha256
        ):
            raise PipelineError("receipt_mismatch", "Comfy receipt input pair differs from the approved child")
        declarations = [item for item in metadata["outputs"]]
        from ..schemas import MediaOutputDeclaration

        declarations = [MediaOutputDeclaration.model_validate(item) for item in declarations]
        discovered = discover_declared_outputs(record, declarations, local_path("output"))
        if len(discovered) != 1 or discovered[0].media_kind != "image":
            raise PipelineError("invalid_output", "ComfyUI must return exactly one inpaint image")
        candidate = discovered[0].path.read_bytes()
        # Publish immutable candidate evidence before decoding.  A malformed
        # PNG therefore cannot be mistaken for a missing provider result.
        source_ids = [
            request.image_mask.image_ref.artifact_id,
            request.image_mask.mask_ref.artifact_id,
        ]
        self.artifacts.put(
            operation.task_id,
            operation.operation_id,
            candidate,
            role="inpaint_candidate",
            media_type="image/png",
            source_ids=source_ids,
        )
        candidate_image = self._read_png(candidate, "candidate image")
        mask = request.image_mask
        if candidate_image.size != (mask.width, mask.height):
            raise RuntimeExecutionError("Generated candidate dimensions do not match the prepared binding")
        if candidate_image.mode != canonical.mode:
            raise RuntimeExecutionError("Generated candidate mode does not match the canonical image")
        reconstruction = restore_to_canonical(canonical, candidate_image, canonical_mask, mask)
        candidate_bytes = self._png(candidate_image)
        reconstruction_bytes = self._png(reconstruction)
        self.artifacts.put(
            operation.task_id,
            operation.operation_id,
            candidate_bytes,
            role="image",
            media_type="image/png",
            source_ids=source_ids,
        )
        self.artifacts.put(
            operation.task_id,
            operation.operation_id,
            reconstruction_bytes,
            role="reconstruction",
            media_type="image/png",
            source_ids=source_ids,
        )
        return [
            ("image", candidate_bytes, "image/png"),
            ("reconstruction", reconstruction_bytes, "image/png"),
        ]

    @staticmethod
    def _png(image: Image.Image) -> bytes:
        stream = io.BytesIO()
        image.save(stream, format="PNG", optimize=False)
        return stream.getvalue()

    def cancel(self, submission: Submission) -> UIObservation:
        operation, _plan, _request, metadata, _record = self._history(submission)
        if metadata.get("no_edit_pixels") or submission.metadata.get("no_edit_pixels"):
            return UIObservation(state="succeeded", actual=Cost())
        try:
            queue = self.backend.client.queue()
        except Exception:
            raise PipelineError("resource_release_unknown") from None
        queued = self._queue_ids(queue)
        if submission.request_id not in queued:
            return self.inspect(submission)
        others = queued - {submission.request_id}
        if others:
            # Comfy's /interrupt endpoint can be global on older servers.  Do
            # not invoke it while another queue item is active.
            raise PipelineError("resource_busy", "Cannot safely interrupt ComfyUI while another job is queued")
        cancel = getattr(self.backend.client, "cancel_owned_prompt", None)
        if not callable(cancel):
            raise PipelineError("resource_release_unknown", "Comfy client cannot cancel an owned prompt")
        cancel(submission.request_id)
        return self.inspect(submission)

    # ------------------------------------------------------------------
    # GPU handoff boundary
    # ------------------------------------------------------------------
    def _assert_owned_resource(self, operation: Any, request_id: str) -> None:
        """Require the ledger's local-gpu lease to name this exact receipt."""

        try:
            with self.ledger.transaction() as db:
                rows = db.execute(
                    "SELECT o.operation_id FROM operations o JOIN resources r "
                    "ON r.operation_id=o.operation_id "
                    "WHERE o.operation_id=? AND o.task_id=? AND o.provider_request_id=? "
                    "AND r.resource='local-gpu'",
                    (operation.operation_id, operation.task_id, request_id),
                ).fetchall()
        except Exception:
            raise PipelineError("operation_scope", "UI inpaint resource ownership cannot be verified") from None
        if len(rows) != 1:
            raise PipelineError("operation_scope", "UI inpaint receipt does not own local-gpu")

    def release_operation(self, submission: Submission) -> dict[str, Any]:
        """Release the provider only after proving this operation owns it."""

        operation, _plan, _request, metadata = self._validated_submission(submission)
        self._assert_owned_resource(operation, submission.request_id)
        if metadata.get("no_edit_pixels") or submission.metadata.get("no_edit_pixels"):
            return {"released": True, "provider": "comfy", "operation_id": operation.operation_id, "noop": True}
        observed = self.inspect(submission)
        if observed.state not in {"succeeded", "failed"}:
            raise PipelineError("resource_release_unknown", "Comfy prompt is not terminal")
        try:
            queue = self.backend.client.queue()
        except Exception:
            raise PipelineError("resource_release_unknown") from None
        queued = self._queue_ids(queue)
        if submission.request_id in queued:
            raise PipelineError("resource_release_unknown", "Owned Comfy prompt remains queued")
        if queued:
            # There is no safe process-wide unload/interrupt while another
            # Comfy operation owns the queue.
            raise PipelineError("resource_busy", "Another Comfy operation is queued")

        releaser = None
        for owner in (self.backend, self.backend.client):
            for name in ("release_models", "release", "release_model", "free", "unload"):
                candidate = getattr(owner, name, None)
                if callable(candidate):
                    releaser = candidate
                    break
            if releaser:
                break
        if releaser is None:
            raise PipelineError(
                "resource_release_unknown",
                "The configured Comfy client has no provider release acknowledgment",
            )
        try:
            response = releaser()
        except Exception:
            raise PipelineError("resource_release_unknown") from None
        if response is True:
            proof = {"released": True}
        elif isinstance(response, dict) and response.get("released") is True:
            proof = {"released": True}
            for key in ("device", "cuda_allocated_bytes", "release_metrics", "comfyui_version", "devices"):
                if key in response:
                    proof[key] = response[key]
        else:
            raise PipelineError("resource_release_unknown")
        proof.update({"provider": "comfy", "operation_id": operation.operation_id, "queue_jobs": 0})
        return proof

    def release(self, submission: Submission | None = None) -> dict[str, Any]:
        if submission is None:
            raise PipelineError("resource_scope", "A provider release must identify the owned operation")
        return self.release_operation(submission)
