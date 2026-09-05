"""Synchronous domain boundary used by Activities and trusted local CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from ..assets.store import ArtifactStore
from ..paths import find_repo_root
from ..schemas import GenerationPlan, TaskBudget
from ..schemas.pipeline import (
    ApprovalEnvelope,
    ArtifactRef,
    Cost,
    OperationRecord,
    PipelinePlan,
    canonical_json,
    digest,
    operation_id,
    validate_payload,
)
from .contracts import Submission
from .errors import OutcomeUnknown, PipelineError
from .ledger import Ledger
from .registry import validate_registration
from .simulation import SimulationBackend


class PipelineService:
    def __init__(
        self, root: Path, *, backends: dict | None = None, mlflow_enabled: bool = False, ui_schema: bool = False
    ) -> None:
        self.root = root.resolve()
        self.artifacts = ArtifactStore(self.root / "artifacts")
        self.ledger = Ledger(self.root / "ledger.sqlite", initialize_ui=ui_schema)
        self.backends = backends or {}
        self.mlflow_enabled = mlflow_enabled

    def backend(self, plan: PipelinePlan):
        if plan.workflow_type == "ui_analysis":
            raise PipelineError("invalid_step", "UI workflows must use the registered step entrypoint")
        capability = validate_registration(plan)
        if capability.id not in self.backends:
            if capability.id == "simulation.generate":
                self.backends[capability.id] = SimulationBackend(self.root / "simulation.sqlite")
            else:
                from .comfy import ComfyOperationBackend

                self.backends[capability.id] = ComfyOperationBackend()
        return self.backends[capability.id]

    def smoke_plan(self, task_id: str, *, revisions: int = 0, accept_after: int = 0) -> PipelinePlan:
        plan = PipelinePlan(
            task_id=task_id,
            workflow_type="temporal_smoke",
            envelope=ApprovalEnvelope(
                allowed_capabilities=["simulation.generate"],
                budget=TaskBudget(
                    max_total_cost_usd=0,
                    max_iteration_cost_usd=0,
                    max_total_gpu_minutes=0,
                    max_iteration_gpu_minutes=0,
                    max_revisions=revisions,
                ),
            ),
            parameters={"accept_after_revision": accept_after},
        )
        self.ledger.register(plan)
        return plan

    def generation_plan(self, source: GenerationPlan) -> PipelinePlan:
        """Stage immutable local copies. User approves the resulting pipeline fingerprint."""
        if source.backend != "comfy" or source.envelope.budget.max_revisions != 0:
            raise PipelineError("invalid_plan", "Comfy pipeline v1 requires a Comfy plan with max_revisions=0")
        if source.envelope.allowed_tools != ["comfy.generate"] or source.envelope.budget.max_iteration_gpu_minutes <= 0:
            raise PipelineError("invalid_plan", "Comfy generation needs its exact tool and a positive GPU budget")
        validate_payload(source.model_dump(mode="json", exclude={"input_assets"}))
        from ..policy import verify_sha256
        from ..policy.gates import sha256_file
        from ..workflows.compiler import load_recipe

        recipe = load_recipe(str(source.recipe))
        root = find_repo_root()
        dependencies = dict(source.dependency_hashes)
        for name, expected in dependencies.items():
            candidate = (root / name).resolve()
            if not candidate.is_relative_to(root):
                raise PipelineError("unsafe_dependency")
            verify_sha256(candidate, expected)
        for name in (
            f"configs/workflows/recipes/{recipe.id}.yaml",
            recipe.base_workflow,
            "configs/models/catalog.yaml",
            "configs/runtime/comfyui.lock.yaml",
        ):
            dependencies[name] = sha256_file(root / name)
        source = source.model_copy(update={"dependency_hashes": dependencies})
        # Import as a new logical task. Never migrate a legacy AgentStore task in place.
        source = source.model_copy(update={"task_id": "pipeline-" + digest(source)[:32]})
        assets = []
        refs = []
        for asset in source.input_assets:
            from ..policy import verify_sha256

            verify_sha256(Path(asset.local_path), asset.sha256)
            verify_sha256(Path(asset.derived_path), asset.derived_sha256)
            original = self.artifacts.put(
                source.task_id,
                "input",
                Path(asset.local_path).read_bytes(),
                role="original",
                media_type=asset.mime_type,
            )
            derived = self.artifacts.put(
                source.task_id,
                "input",
                Path(asset.derived_path).read_bytes(),
                role="derived",
                media_type="image/png",
                source_ids=[original.artifact_id],
            )
            assets.append(
                asset.model_copy(
                    update={
                        "safe_source": original.artifact_id,
                        "local_path": str(self.artifacts.resolve(original)),
                        "derived_path": str(self.artifacts.resolve(derived)),
                    }
                )
            )
            refs.extend([original, derived])
        effective = source.model_copy(update={"input_assets": assets})
        ref = self.artifacts.put(source.task_id, "plan", canonical_json(effective).encode(), role="generation_plan")
        plan = PipelinePlan(
            task_id=source.task_id,
            workflow_type="comfy_generation",
            inputs=refs,
            generation_plan=ref,
            envelope=ApprovalEnvelope(allowed_capabilities=["comfy.generate"], budget=source.envelope.budget),
            estimated_iteration=Cost(
                cost_usd=source.estimated_iteration_cost_usd,
                gpu_minutes=source.estimated_iteration_gpu_minutes,
            ),
            dependency_hashes=source.dependency_hashes,
        )
        self.ledger.register(plan)
        return plan

    def checked_plan(self, task_id: str, fingerprint: str, *, verify_inputs: bool = True) -> PipelinePlan:
        plan = self.ledger.plan(task_id)
        if fingerprint != plan.fingerprint:
            raise PipelineError("plan_changed", "Plan fingerprint does not match the persisted task")
        validate_registration(plan)
        if plan.workflow_type == "ui_analysis" and verify_inputs:
            from .registry import validate_ui_registration

            validate_ui_registration(plan, self.artifacts)
        if not verify_inputs:
            return plan
        for ref in plan.inputs:
            self.artifacts.read(ref)
        if plan.generation_plan:
            self.artifacts.read(plan.generation_plan)
        from ..policy import verify_sha256

        root = find_repo_root()
        for name, expected in plan.dependency_hashes.items():
            path = (root / name).resolve()
            if not path.is_relative_to(root):
                raise PipelineError("unsafe_dependency", "Dependency escapes repository scope")
            try:
                verify_sha256(path, expected)
            except Exception as exc:
                raise PipelineError("dependency_changed", "Frozen dependency changed or is unavailable") from exc
        return plan

    def submit(self, plan: PipelinePlan, revision: int) -> OperationRecord:
        try:
            existing = self.ledger.get(operation_id(plan, "generate", revision))
        except PipelineError as exc:
            if exc.code != "unknown_operation":
                raise
            existing = None
        if existing is None or existing.state == "prepared":
            try:
                self.checked_plan(plan.task_id, plan.fingerprint)
            except PipelineError:
                if existing is not None:
                    self.ledger.finish(existing.operation_id, Cost(), {"input_validation_failed": True}, failed=True)
                raise
        backend = self.backend(plan)
        operation = self.ledger.reserve(
            plan,
            "generate",
            revision,
            (
                Cost(
                    cost_usd=plan.envelope.budget.max_iteration_cost_usd,
                    gpu_minutes=plan.envelope.budget.max_iteration_gpu_minutes,
                )
                if plan.workflow_type == "comfy_generation"
                else plan.estimated_iteration
            ),
            resource=backend.capability.resource,
        )
        if operation.state in {"submitted", "running", "succeeded", "failed"}:
            return operation
        if operation.state != "prepared" and not backend.capability.idempotent_submission:
            return self.recover(plan, operation.operation_id)
        arguments = {"revision": revision}
        if plan.generation_plan:
            arguments["generation_plan"] = json.loads(self.artifacts.read(plan.generation_plan))
        if operation.state == "prepared" and hasattr(backend, "prepare"):
            try:
                arguments["prepared"] = backend.prepare(operation.operation_id, arguments)
            except Exception:
                return self.ledger.finish(operation.operation_id, Cost(), {"preparation_failed": True}, failed=True)
        can_submit = self.ledger.begin_submit(operation.operation_id)
        if not can_submit and not backend.capability.idempotent_submission:
            return self.recover(plan, operation.operation_id)
        try:
            receipt = backend.submit(operation.operation_id, arguments)
            return self.ledger.submitted(operation.operation_id, receipt.request_id, {"receipt": receipt.metadata})
        except Exception as exc:
            self.ledger.uncertain(operation.operation_id)
            raise OutcomeUnknown() from exc

    def recover(self, plan: PipelinePlan, key: str) -> OperationRecord:
        operation = self.ledger.get(key)
        if operation.task_id != plan.task_id:
            raise PipelineError("operation_scope")
        if operation.state in {"succeeded", "failed", "submitted", "running"}:
            return operation
        backend = self.backend(plan)
        try:
            if operation.provider_request_id:
                return operation
            recover = getattr(backend, "recover", None)
            receipt = recover(key) if recover else None
            if receipt is not None:
                return self.ledger.submitted(key, receipt.request_id, {"receipt": receipt.metadata})
        except Exception:
            pass
        self.ledger.uncertain(key)
        raise OutcomeUnknown()

    def observe(self, plan: PipelinePlan, key: str) -> OperationRecord:
        operation = self.ledger.get(key)
        if operation.task_id != plan.task_id:
            raise PipelineError("operation_scope")
        if operation.state in {"succeeded", "failed"}:
            return operation
        if not operation.provider_request_id:
            operation = self.recover(plan, key)
        receipt = Submission(request_id=operation.provider_request_id, metadata=operation.result.get("receipt", {}))
        try:
            observed = self.backend(plan).inspect(receipt)
        except Exception as exc:
            self.ledger.uncertain(key)
            raise OutcomeUnknown() from exc
        if observed.state == "unknown":
            self.ledger.uncertain(key)
            raise OutcomeUnknown()
        result = {**operation.result, "observation": observed.model_dump(mode="json")}
        validate_payload(result)
        with self.ledger.transaction() as db:
            db.execute(
                """UPDATE operations SET state='running',result=?
                WHERE operation_id=? AND state NOT IN ('succeeded','failed')""",
                (canonical_json(result), key),
            )
        return self.ledger.get(key)

    def collect(self, plan: PipelinePlan, key: str) -> OperationRecord:
        operation = self.ledger.get(key)
        if operation.task_id != plan.task_id:
            raise PipelineError("operation_scope")
        if operation.state in {"succeeded", "failed"}:
            for item in operation.result.get("artifacts", []):
                self.artifacts.read(ArtifactRef.model_validate(item))
            return operation
        observation = operation.result.get("observation", {})
        if observation.get("state") not in {"succeeded", "failed"}:
            raise PipelineError("operation_pending", "Provider has not confirmed terminal completion")
        actual = Cost.model_validate(observation["actual"])
        outputs = []
        if observation["state"] == "succeeded":
            receipt = Submission(request_id=operation.provider_request_id, metadata=operation.result.get("receipt", {}))
            for role, data, media_type in self.backend(plan).collect(receipt):
                outputs.append(
                    self.artifacts.put(
                        plan.task_id,
                        key,
                        data,
                        role=role,
                        media_type=media_type,
                        source_ids=[ref.artifact_id for ref in plan.inputs],
                    ).model_dump(mode="json")
                )
        return self.ledger.finish(
            key,
            actual,
            {**operation.result, "artifacts": outputs},
            failed=observation["state"] == "failed",
        )

    def cancel(self, plan: PipelinePlan, revision: int) -> bool:
        self.ledger.request_cancel(plan.task_id)
        key = operation_id(plan, "generate", revision)
        try:
            operation = self.ledger.get(key)
        except PipelineError as exc:
            if exc.code == "unknown_operation":
                return True
            raise
        if operation.state in {"succeeded", "failed"}:
            return True
        if operation.state == "prepared":
            self.ledger.finish(key, Cost(), {"cancelled_before_submission": True}, failed=True)
            return True
        if operation.provider_request_id:
            try:
                self.backend(plan).cancel(
                    Submission(
                        request_id=operation.provider_request_id,
                        metadata=operation.result.get("receipt", {}),
                    )
                )
                operation = self.observe(plan, key)
                if operation.result.get("observation", {}).get("state") in {"succeeded", "failed"}:
                    self.collect(plan, key)
                    return True
            except Exception:
                pass
        self.ledger.uncertain(key)
        return False

    def ui_plan(self, task_id: str, request) -> PipelinePlan:
        """Trusted local planning only; input references must already belong to the task."""
        from ..schemas.ui import UIAnalysisRequest, UIEvaluationPolicy, UIPolicy
        from .registry import ui_capabilities, validate_ui_registration

        request = UIAnalysisRequest.model_validate(request.model_dump(mode="json"))
        with self.ledger.transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {2, 3}:
                raise PipelineError("migration_required", "Stop writers and migrate the UI ledger to v2")
        for ref in request.references():
            if ref.task_id != task_id:
                raise PipelineError("artifact_scope")
            self.artifacts.read(ref)
        policy = UIPolicy(resources=request.resources, limits=request.limits)
        params = {
            name + "_ref": self.artifacts.put(task_id, "plan", canonical_json(value).encode(), role=name).model_dump(
                mode="json"
            )
            for name, value in {"request": request, "policy": policy, "evaluation": UIEvaluationPolicy()}.items()
        }
        plan = PipelinePlan(
            task_id=task_id,
            workflow_type="ui_analysis",
            parameters=params,
            inputs=request.references(),
            envelope=ApprovalEnvelope(
                stage="analysis", allowed_capabilities=ui_capabilities(request), budget=request.budget
            ),
        )
        validate_ui_registration(plan, self.artifacts)
        self.ledger.register(plan)
        return plan

    def ui_backend(self, plan: PipelinePlan, binding):
        from .registry import resolve_ui_step

        capability = resolve_ui_step(plan, binding)
        backend = self.backends.get(capability.id)
        if backend is None:
            raise PipelineError("capability_not_ready", "The requested UI capability is not configured")
        if backend.capability.id != capability.id:
            raise PipelineError("prohibited_capability")
        return backend

    def submit_step(self, plan: PipelinePlan, binding, reservation: Cost) -> OperationRecord:
        from ..schemas.ui import UIStepBinding
        from .registry import validate_ui_registration

        binding = UIStepBinding.model_validate(binding.model_dump(mode="json"))
        plan = self.checked_plan(plan.task_id, plan.fingerprint)
        request = validate_ui_registration(plan, self.artifacts)
        if binding.outputs:
            raise PipelineError("invalid_step", "A submission cannot supply its own result references")
        for ref in [*binding.inputs, *([binding.parameters_ref] if binding.parameters_ref else [])]:
            self.artifacts.read(ref)
        backend = self.ui_backend(plan, binding)
        try:
            operation = self.ledger.reserve(
                plan,
                binding.step_id,
                binding.revision,
                reservation,
                resource=backend.capability.resource,
                ui_binding=binding,
                ui_request=request,
            )
        except PipelineError as exc:
            if exc.code in {"iteration_budget", "total_budget"}:
                raise PipelineError(
                    "budget_insufficient", "The next operation does not fit the remaining budget"
                ) from None
            raise
        if operation.state in {"succeeded", "failed", "submitted", "running"}:
            for item in operation.result.get("artifacts", []):
                self.artifacts.read(ArtifactRef.model_validate(item))
            return operation
        if operation.state != "prepared" or not self.ledger.begin_submit(operation.operation_id):
            return self.recover_step(plan, operation.operation_id)
        try:
            receipt = backend.submit(operation.operation_id, {"binding": binding.model_dump(mode="json")})
            return self.ledger.submitted(operation.operation_id, receipt.request_id, {"receipt": receipt.metadata})
        except Exception:
            self.ledger.uncertain(operation.operation_id)
            raise OutcomeUnknown() from None

    def recover_step(self, plan: PipelinePlan, key: str) -> OperationRecord:
        operation = self.ledger.get(key)
        if operation.task_id != plan.task_id:
            raise PipelineError("operation_scope")
        binding = self.ledger.ui_binding(key)
        backend = self.ui_backend(plan, binding)
        if operation.state in {"succeeded", "failed", "submitted", "running"} or operation.provider_request_id:
            return operation
        try:
            recover = getattr(backend, "recover", None)
            receipt = recover(key) if recover else None
            if receipt is not None:
                return self.ledger.submitted(key, receipt.request_id, {"receipt": receipt.metadata})
        except Exception:
            pass
        self.ledger.uncertain(key)
        raise OutcomeUnknown()

    def observe_step(self, plan: PipelinePlan, key: str) -> OperationRecord:
        from ..schemas.ui import UIObservation

        operation = self.ledger.get(key)
        if operation.task_id != plan.task_id:
            raise PipelineError("operation_scope")
        if operation.state in {"succeeded", "failed"}:
            return operation
        if not operation.provider_request_id:
            operation = self.recover_step(plan, key)
        binding = self.ledger.ui_binding(key)
        receipt = Submission(request_id=operation.provider_request_id, metadata=operation.result.get("receipt", {}))
        try:
            observed = self.ui_backend(plan, binding).inspect(receipt)
            observed = UIObservation.model_validate(observed.model_dump(mode="json"))
        except Exception:
            self.ledger.uncertain(key)
            raise OutcomeUnknown() from None
        if observed.state == "unknown" or (observed.state in {"succeeded", "failed"} and observed.actual is None):
            self.ledger.uncertain(key)
            raise OutcomeUnknown()
        result = {**operation.result, "observation": observed.model_dump(mode="json")}
        validate_payload(result)
        with self.ledger.transaction() as db:
            db.execute(
                "UPDATE operations SET state='running',result=? WHERE operation_id=? "
                "AND state NOT IN ('succeeded','failed')",
                (canonical_json(result), key),
            )
        return self.ledger.get(key)

    def collect_step(self, plan: PipelinePlan, key: str) -> OperationRecord:
        from ..schemas.ui import UIObservation
        from .registry import UI_OUTPUT_ROLES

        operation = self.ledger.get(key)
        if operation.task_id != plan.task_id:
            raise PipelineError("operation_scope")
        if operation.state in {"succeeded", "failed"}:
            for item in operation.result.get("artifacts", []):
                self.artifacts.read(ArtifactRef.model_validate(item))
            return operation
        observed = UIObservation.model_validate(
            operation.result.get("observation", {"state": "unknown", "actual": None})
        )
        if observed.state not in {"succeeded", "failed"}:
            raise PipelineError("operation_pending")
        if observed.actual is None:
            self.ledger.uncertain(key)
            raise OutcomeUnknown()
        binding = self.ledger.ui_binding(key)
        outputs = []
        if observed.state == "succeeded":
            receipt = Submission(request_id=operation.provider_request_id, metadata=operation.result.get("receipt", {}))
            try:
                for role, data, media_type in self.ui_backend(plan, binding).collect(receipt):
                    if role not in UI_OUTPUT_ROLES[binding.capability] or len(outputs) >= 32:
                        raise PipelineError("invalid_output", "UI outputs require registered roles and bounded indexes")
                    outputs.append(
                        self.artifacts.put(
                            plan.task_id,
                            key,
                            data,
                            role=role,
                            media_type=media_type,
                            source_ids=[ref.artifact_id for ref in binding.inputs],
                        ).model_dump(mode="json")
                    )
            except Exception:
                self.ledger.uncertain(key)
                raise OutcomeUnknown() from None
        return self.ledger.finish_ui(
            key, observed.actual, {**operation.result, "artifacts": outputs}, failed=observed.state == "failed"
        )
