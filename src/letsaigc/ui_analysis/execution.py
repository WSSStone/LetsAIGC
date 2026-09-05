"""Fixed single-image step preparation and pure local adapters for Temporal."""

import json
from typing import Literal

from pydantic import Field

from ..pipelines.contracts import Capability, Submission
from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, Cost, PipelineModel, canonical_json, operation_id
from ..schemas.ui import CanonicalImage, ImageView, UIAnalysisRequest, UIObservation, UIStepBinding
from ..schemas.ui_provider import UIProvisionResult
from ..tracking.manifest import create_ui_manifest
from ..ui_providers.base import ProvisionContext
from ..ui_providers.manual import ManualUIProvider
from ..vision.ocr import merge_ocr_results
from .crops import create_crops
from .layout import build_layout
from .normalize import make_views, normalize

UIPhase = Literal["provide", "normalize", "ocr", "analyze", "layout", "crop", "project"]
PHASES = ("provide", "normalize", "ocr", "analyze", "layout", "crop", "project")


class UIStepPreparation(PipelineModel):
    binding: UIStepBinding | None
    reservation: Cost = Field(default_factory=Cost)
    repeat_count: int = Field(default=1, ge=1, le=64, strict=True)
    context_refs: list[ArtifactRef] = Field(default_factory=list, max_length=8)
    active_limit_seconds: int = Field(default=3600, ge=1, le=3600, strict=True)


class LocalUIBackend:
    def __init__(self, execution, capability):
        self.execution = execution
        self.capability = Capability(id=capability, idempotent_submission=True)

    def submit(self, key, arguments):
        binding = UIStepBinding.model_validate(arguments["binding"])
        outputs = self.execution.compute(binding)
        return Submission(
            request_id="local-" + key, metadata={"outputs": [ref.model_dump(mode="json") for ref in outputs]}
        )

    def recover(self, key):
        binding = self.execution.service.ledger.ui_binding(key)
        return self.submit(key, {"binding": binding.model_copy(update={"outputs": []}).model_dump(mode="json")})

    def inspect(self, receipt):
        return UIObservation(state="succeeded", actual=Cost())

    def collect(self, receipt):
        refs = [ArtifactRef.model_validate(ref) for ref in receipt.metadata["outputs"]]
        return [(ref.role, self.execution.service.artifacts.read(ref), ref.media_type) for ref in refs]


class UIExecution:
    def __init__(self, service, *, evidence_kind="runtime"):
        self.service = service
        self.store = service.artifacts
        self.evidence_kind = evidence_kind
        for name in ("manual", "search", "normalize", "layout", "crop", "project"):
            service.backends.setdefault("ui." + name, LocalUIBackend(self, "ui." + name))

    def request(self, plan):
        return UIAnalysisRequest.model_validate_json(
            self.store.read(ArtifactRef.model_validate(plan.parameters["request_ref"]))
        )

    def ref(self, plan, step, role):
        operation = self.service.ledger.get(operation_id(plan, step, 0))
        if operation.state != "succeeded":
            raise PipelineError("dependency_not_ready")
        refs = [ArtifactRef.model_validate(item) for item in operation.result["artifacts"] if item["role"] == role]
        if len(refs) != 1:
            raise PipelineError("invalid_output")
        self.store.read(refs[0])
        return refs[0]

    def canonical(self, plan):
        index = json.loads(self.store.read(self.ref(plan, "normalize", "canonical_index")))
        return CanonicalImage.model_validate(index["canonical"]), [
            ArtifactRef.model_validate(item) for item in index["views"]
        ]

    def prepare(self, plan, phase: UIPhase, position: int) -> UIStepPreparation:
        request = self.request(plan)
        from .resources import check_request_resources

        check_request_resources(self.store, plan.task_id, request)
        if phase not in PHASES or type(position) is not int or not 0 <= position < 64:
            raise PipelineError("invalid_step")
        capability = "ui." + (request.input.kind if phase == "provide" else phase)
        step = capability.removeprefix("ui.")
        params, dependencies, context_refs = None, {}, []
        repeat = 1
        if phase == "provide":
            inputs = request.references()
        elif phase == "normalize":
            inputs = [self.ref(plan, request.input.kind, "sources")]
        elif phase == "ocr":
            canonical, views = self.canonical(plan)
            tiles = [ref for ref in views if ImageView.model_validate_json(self.store.read(ref)).kind == "ocr_tile"]
            repeat = len(tiles)
            if position >= repeat:
                return UIStepPreparation(binding=None, repeat_count=repeat)
            inputs = [tiles[position]]
            step = f"ocr.{position}"
            model = request.model_bindings.get("ocr")
            if model:
                dependencies["ocr-model"] = model.sha256
            params = self.store.put(
                plan.task_id, "prepare", canonical_json({"language": request.language}).encode(), role="ocr_parameters"
            )
        elif phase == "analyze":
            _, views = self.canonical(plan)
            tiles = [ref for ref in views if ImageView.model_validate_json(self.store.read(ref)).kind == "ocr_tile"]
            refs = [self.ref(plan, f"ocr.{index}", "texts") for index in range(len(tiles))]
            texts = merge_ocr_results([json.loads(self.store.read(ref)) for ref in refs])
            texts_ref = self.store.put(
                plan.task_id,
                "ocr-merge",
                canonical_json(texts).encode(),
                role="texts",
                source_ids=[ref.artifact_id for ref in refs],
            )
            overview = next(
                ref for ref in views if ImageView.model_validate_json(self.store.read(ref)).kind == "overview"
            )
            inputs = [overview, texts_ref]
            context_refs = [texts_ref]
        elif phase == "layout":
            analyzed = self.service.ledger.ui_binding(operation_id(plan, "analyze", 0))
            inputs = [
                self.ref(plan, "analyze", "analysis"),
                self.ref(plan, "normalize", "canonical_index"),
                next(ref for ref in analyzed.inputs if ref.role == "texts"),
            ]
        elif phase == "crop":
            inputs = [self.ref(plan, "layout", "layout"), self.ref(plan, "normalize", "canonical_index")]
        else:
            inputs = [ref for ref in self.outputs(plan) if ref.role != "manifest"]
        if phase != "ocr" and position:
            return UIStepPreparation(binding=None)
        binding = UIStepBinding(
            task_id=plan.task_id,
            step_id=step,
            capability=capability,
            inputs=inputs,
            parameters_ref=params,
            dependency_hashes=dependencies,
        )
        return UIStepPreparation(
            binding=binding,
            repeat_count=repeat,
            context_refs=context_refs,
            reservation=Cost(cost_usd=request.budget.max_iteration_cost_usd) if phase == "analyze" else Cost(),
            active_limit_seconds=request.resources.active_seconds,
        )

    def outputs(self, plan):
        refs = {}
        for op in self.service.ledger.list_operations(plan.task_id):
            if op.state not in {"succeeded", "failed"} or op.step_id.startswith("ocr."):
                continue
            for item in op.result.get("artifacts", []):
                ref = ArtifactRef.model_validate(item)
                refs[(ref.role, ref.sha256)] = ref
            if op.step_id == "analyze":
                binding = self.service.ledger.ui_binding(op.operation_id)
                for ref in binding.inputs:
                    if ref.role == "texts":
                        refs[(ref.role, ref.sha256)] = ref
        return list(refs.values())

    def compute(self, binding):
        plan = self.service.ledger.plan(binding.task_id)
        request = self.request(plan)
        key = operation_id(plan, binding.step_id, binding.revision)

        def put(role, value, *, media_type="application/json"):
            data = value if isinstance(value, bytes) else canonical_json(value).encode()
            return self.store.put(
                plan.task_id,
                key,
                data,
                role=role,
                media_type=media_type,
                source_ids=[ref.artifact_id for ref in binding.inputs],
            )

        if binding.capability == "ui.manual":
            result = ManualUIProvider().provide(
                request.input,
                ProvisionContext(
                    plan.task_id, key, tuple(plan.envelope.allowed_capabilities), self.store, self.service.ledger
                ),
            )
            if result.status != "ready":
                raise PipelineError("invalid_input")
            return [put("sources", result)]
        if binding.capability == "ui.search":
            from ..ui_providers.search import SearchAcquisition

            result = SearchAcquisition(self.service).result(plan)
            if result.status != "ready":
                raise PipelineError("input_resupply_required")
            return [put("sources", result)]
        if binding.capability == "ui.normalize":
            source = UIProvisionResult.model_validate_json(self.store.read(binding.inputs[0])).sources[0]
            canonical, transforms = normalize(self.store, source.original_ref, key, request.resources)
            canonical = canonical.model_copy(update={"source_id": source.source_id})
            views = make_views(self.store, canonical, key, request.resources)
            view_refs = [put("view_manifest", view) for view in views]
            return [
                canonical.canonical_ref,
                put("transforms", transforms),
                put(
                    "canonical_index",
                    {
                        "canonical": canonical.model_dump(mode="json"),
                        "views": [ref.model_dump(mode="json") for ref in view_refs],
                    },
                ),
            ]
        if binding.capability == "ui.layout":
            canonical, _ = self.canonical(plan)
            analysis_ref = next(ref for ref in binding.inputs if ref.role == "analysis")
            text_ref = next(ref for ref in binding.inputs if ref.role == "texts")
            result = json.loads(self.store.read(analysis_ref))
            layout = build_layout(
                canonical,
                ImageView.model_validate(result["view"]),
                json.loads(self.store.read(text_ref)),
                result["output"],
            )
            layout.update(texts_ref=text_ref.model_dump(mode="json"), analysis_ref=analysis_ref.model_dump(mode="json"))
            return [put("layout", layout), put("quality_report", layout["quality"])]
        if binding.capability == "ui.crop":
            canonical, _ = self.canonical(plan)
            layout = json.loads(self.store.read(next(ref for ref in binding.inputs if ref.role == "layout")))
            result = create_crops(self.store, canonical, layout, key, resources=request.resources)
            payload = {
                "schema_version": 1,
                "overlay_ref": result["overlay_ref"].model_dump(mode="json"),
                "overlay_labels": result["overlay_labels"],
                "crops": [{**item, "ref": item["ref"].model_dump(mode="json")} for item in result["crops"]],
            }
            return [result["overlay_ref"], put("asset_index", payload)]
        if binding.capability == "ui.project":
            return [create_ui_manifest(self.service, plan, binding.inputs, evidence_kind=self.evidence_kind)]
        raise PipelineError("prohibited_capability")

    def cancel(self, plan):
        self.service.ledger.request_cancel(plan.task_id)
        confirmed = True
        for operation in self.service.ledger.list_operations(plan.task_id):
            if operation.state in {"succeeded", "failed"}:
                continue
            if operation.state == "prepared":
                self.service.ledger.finish_ui(
                    operation.operation_id, Cost(), {"cancelled_before_submission": True}, failed=True
                )
                continue
            try:
                binding = self.service.ledger.ui_binding(operation.operation_id)
                backend = self.service.ui_backend(plan, binding)
                if operation.provider_request_id and hasattr(backend, "cancel"):
                    backend.cancel(
                        Submission(
                            request_id=operation.provider_request_id, metadata=operation.result.get("receipt", {})
                        )
                    )
                observed = self.service.observe_step(plan, operation.operation_id)
                if observed.result.get("observation", {}).get("state") in {"succeeded", "failed"}:
                    self.service.collect_step(plan, operation.operation_id)
                else:
                    confirmed = False
            except Exception:
                self.service.ledger.uncertain(operation.operation_id)
                confirmed = False
        return confirmed
