from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..assets import AssetResolver
from ..backends.base import GenerationBackend, GenerationResult
from ..backends.comfy import ComfyBackend
from ..backends.media import MediaToolBackend
from ..backends.openai_image import OpenAIImageBackend, image_model_snapshot
from ..comfy import ComfyClient
from ..config import load_catalog, load_typed
from ..errors import PolicyError, ReadinessError, ValidationError
from ..generation.pricing import image_output_reservation, load_pricing
from ..generation.router import CapabilityRouter, default_capabilities
from ..models import ModelManager
from ..paths import find_repo_root
from ..policy import verify_sha256
from ..policy.gates import sha256_file
from ..schemas import (
    AgentCandidate,
    AgentIteration,
    AgentRunMetadata,
    AgentSession,
    AgentTaskRecord,
    AgentTaskState,
    AgentTurn,
    BackendCapability,
    BackendName,
    ExecutionEnvelope,
    GenerationIntent,
    GenerationPlan,
    LicenseLane,
    TaskBudget,
)
from ..tracking import load_manifest, save_manifest
from ..tracking.manifest import add_output, create_manifest
from ..tracking.mlflow_store import log_manifest
from .approval import BudgetLedger, approve_plan, canonical_json, plan_fingerprint
from .critic import AgentCritic, validate_revision
from .responses import (
    DEFAULT_DECISION_MODEL,
    DEFAULT_VLM_MODEL,
    ResponsesAgentModel,
    load_llm_api_key,
)
from .storage import AgentStore

ALLOWED_TOOL_NAMES = {"comfy.generate", "openai.image", "sprites.build", "drama.render"}
ALLOWED_TRANSITIONS = {
    AgentTaskState.intake: {AgentTaskState.resolve_assets},
    AgentTaskState.resolve_assets: {AgentTaskState.interpret_intent},
    AgentTaskState.interpret_intent: {AgentTaskState.inspect_capabilities},
    AgentTaskState.inspect_capabilities: {AgentTaskState.draft_plan},
    AgentTaskState.draft_plan: {AgentTaskState.validate_budget},
    AgentTaskState.validate_budget: {AgentTaskState.awaiting_approval},
    AgentTaskState.awaiting_approval: {AgentTaskState.execute, AgentTaskState.rejected},
    AgentTaskState.execute: {AgentTaskState.normalize_output, AgentTaskState.finalize},
    AgentTaskState.normalize_output: {AgentTaskState.evaluate},
    AgentTaskState.evaluate: {AgentTaskState.accepted, AgentTaskState.revise, AgentTaskState.finalize},
    AgentTaskState.revise: {AgentTaskState.execute},
    AgentTaskState.accepted: {AgentTaskState.finalize},
    AgentTaskState.rejected: {AgentTaskState.finalize},
    AgentTaskState.finalize: set(),
    AgentTaskState.failed: {AgentTaskState.finalize},
}


class AgentOrchestrator:
    def __init__(
        self,
        *,
        store: AgentStore | None = None,
        model: Any | None = None,
        resolver: AssetResolver | None = None,
        capabilities: list[BackendCapability] | None = None,
        backends: dict[str, GenerationBackend] | None = None,
    ) -> None:
        self.store = store or AgentStore()
        self.model = model or ResponsesAgentModel()
        self.resolver = resolver or AssetResolver()
        self._fixed_capabilities = capabilities
        self.backends = backends or {}

    def plan(
        self,
        intent: str,
        *,
        image_sources: list[str] | None,
        requested_backend: BackendName,
        budget: TaskBudget,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if not intent.strip() or len(intent) > 2000:
            raise ValidationError("Generation intent must contain 1..2000 characters")
        session = self._session(session_id)
        task_id = f"task-{uuid.uuid4().hex[:16]}"
        task = AgentTaskRecord(id=task_id, session_id=session.id, state=AgentTaskState.intake)
        self.store.save_task(task)
        try:
            return self._draft_task(task, session, intent, image_sources, requested_backend, budget)
        except Exception:
            task.state = AgentTaskState.failed
            if task.state_history[-1] != AgentTaskState.failed:
                task.state_history.append(AgentTaskState.failed)
            task.stop_reason = "planning_failed"
            self.store.save_task(task)
            raise

    def _draft_task(self, task, session, intent, image_sources, requested_backend, budget):
        self._transition(task, AgentTaskState.resolve_assets)
        assets = self.resolver.resolve_many(image_sources or [], self.store.inputs_dir(session.id))
        self._transition(task, AgentTaskState.interpret_intent)
        capabilities = self._fixed_capabilities or default_capabilities()
        planning_ledger = BudgetLedger(budget, task.budget_usage)
        try:
            planning_ledger.reserve(cost_usd=0.03 + 0.01 * len(assets))
            self.store.save_task(task)
            if isinstance(self.model, ResponsesAgentModel):
                self.model.context = [
                    {"role": turn.role, "content": turn.content[:1000]}
                    for turn in session.turns[-4:]
                ]
            interpretation = self.model.interpret(
                intent,
                assets,
                [item.model_dump(mode="json") for item in capabilities],
            )
            task.budget_usage = planning_ledger.settle(
                cost_usd=float(getattr(self.model, "last_cost_usd", 0))
            )
            self.store.save_task(task)
        except Exception:
            if planning_ledger.usage.reserved_cost_usd:
                # A transport failure can still have been billed. Keep the full
                # reservation as conservatively accounted usage, never retry it.
                planning_ledger.abandon()
            task.budget_usage = planning_ledger.usage
            task.state = AgentTaskState.failed
            task.state_history.append(AgentTaskState.failed)
            task.stop_reason = "planning_failed"
            self.store.save_task(task)
            raise
        try:
            generation_intent = GenerationIntent(interpretation["intent"])
        except (KeyError, ValueError) as exc:
            raise ValidationError("Agent returned an unsupported generation intent") from exc
        if generation_intent in {GenerationIntent.image_to_image, GenerationIntent.image_to_video} and not assets:
            raise ValidationError(f"{generation_intent.value} requires at least one input image")
        if assets and generation_intent == GenerationIntent.text_to_image:
            generation_intent = GenerationIntent.image_to_image
        self._transition(task, AgentTaskState.inspect_capabilities)
        runtime_capabilities = self._inspect_capabilities(generation_intent, capabilities)
        requested_parameters = interpretation.get("parameters") or {}
        for item in runtime_capabilities:
            if item.backend == "comfy" and (
                len(assets) > 1 or requested_parameters.get("background") == "transparent"
            ):
                item.available = False
                item.reason = "Local recipes accept one input and do not guarantee transparent output"
        capability = CapabilityRouter(runtime_capabilities).route(generation_intent, requested_backend)
        self._transition(task, AgentTaskState.draft_plan)
        generation_plan = self._build_plan(
            task_id=task.id,
            session_id=session.id,
            user_intent=intent,
            interpreted=interpretation,
            intent=generation_intent,
            backend=capability.backend,
            assets=assets,
            budget=budget,
        )
        generation_plan.agent_model = getattr(self.model, "model", DEFAULT_DECISION_MODEL)
        generation_plan.agent_vlm_model = getattr(self.model, "vlm_model", DEFAULT_VLM_MODEL)
        generation_plan.agent_endpoint_fingerprint = getattr(self.model, "endpoint_fingerprint", None)
        generation_plan.dependency_hashes = self._plan_dependencies(generation_plan)
        self._transition(task, AgentTaskState.validate_budget)
        self._validate_estimate(generation_plan, spent_cost_usd=task.budget_usage.actual_cost_usd)
        task.plan = generation_plan
        self._transition(task, AgentTaskState.awaiting_approval)
        session.current_task_id = task.id
        session.turns.append(AgentTurn(role="user", content=intent, task_id=task.id))
        session.turns.append(
            AgentTurn(
                role="assistant",
                content=f"Plan {task.id} is awaiting approval: {plan_fingerprint(generation_plan)}",
                task_id=task.id,
            )
        )
        session.updated_at = datetime.now(UTC)
        self.store.save_session(session)
        payload = json.loads(task.model_dump_json())
        payload["plan_fingerprint"] = plan_fingerprint(generation_plan)
        return payload

    def execute(self, task_id: str, *, approval_fingerprint: str) -> dict[str, Any]:
        with self.store.execution_lock(task_id):
            return self._execute_approved(task_id, approval_fingerprint=approval_fingerprint)

    def _execute_approved(self, task_id: str, *, approval_fingerprint: str) -> dict[str, Any]:
        task = self.store.load_task(task_id)
        if task.state != AgentTaskState.awaiting_approval or task.plan is None:
            raise PolicyError(f"Task is not awaiting approval: {task.state.value}")
        task.approval = approve_plan(task.plan, approval_fingerprint)
        if (
            task.plan.agent_model != getattr(self.model, "model", DEFAULT_DECISION_MODEL)
            or task.plan.agent_vlm_model != getattr(self.model, "vlm_model", DEFAULT_VLM_MODEL)
            or task.plan.agent_endpoint_fingerprint != getattr(self.model, "endpoint_fingerprint", None)
        ):
            raise PolicyError("Agent model or endpoint changed after planning; create and approve a new plan")
        self._verify_plan_inputs(task.plan)
        self._validate_estimate(
            task.plan,
            spent_cost_usd=task.budget_usage.actual_cost_usd,
        )
        approved_tools = task.plan.envelope.allowed_tools
        if len(approved_tools) != 1 or approved_tools[0] not in ALLOWED_TOOL_NAMES:
            raise PolicyError("Approved plan contains a prohibited or ambiguous tool envelope")
        parent = self._create_parent_manifest(task)
        task.run_id = parent.run_id
        self._transition(task, AgentTaskState.execute)
        ledger = BudgetLedger(task.plan.envelope.budget, task.budget_usage)
        current_parameters = dict(task.plan.parameters)
        best_rank = (-1, -1.0)
        terminal_error: Exception | None = None
        for index in range(task.plan.envelope.budget.max_revisions + 1):
            iteration_id = f"iter-{index:02d}"
            effective_plan = task.plan.model_copy(update={"parameters": current_parameters}, deep=True)
            tool_name = effective_plan.envelope.allowed_tools[0]
            if tool_name not in ALLOWED_TOOL_NAMES:
                raise PolicyError(f"Approved plan contains a prohibited tool: {tool_name}")
            arguments_hash = hashlib.sha256(
                canonical_json(
                    {
                        "tool": tool_name,
                        "parameters": current_parameters,
                        "assets": [asset.sha256 for asset in task.plan.input_assets],
                    }
                )
            ).hexdigest()
            iteration = AgentIteration(
                id=iteration_id,
                index=index,
                parameters=current_parameters,
                tool_name=tool_name,
                tool_arguments_hash=arguments_hash,
            )
            task.iterations.append(iteration)
            self.store.save_task(task)
            result: GenerationResult | None = None
            child_run_id: str | None = None
            try:
                self._verify_plan_inputs(effective_plan)
                ledger.reserve(
                    cost_usd=effective_plan.estimated_iteration_cost_usd,
                    gpu_minutes=effective_plan.estimated_iteration_gpu_minutes,
                )
                self.store.save_task(task)
                backend = self._backend_for_tool(tool_name)
                result = backend.execute(effective_plan, iteration_id=iteration_id)
                child_run_id = self._record_child(task, iteration, result, parent.run_id)
                hashes = [sha256_file(path) for path in result.outputs]
                candidate = AgentCandidate(
                    iteration_id=iteration_id,
                    run_id=child_run_id,
                    outputs=[str(path.resolve()) for path in result.outputs],
                    output_hashes=hashes,
                )
                iteration.provider_request_id = result.request_id
                iteration.usage = result.usage
                iteration.candidate = candidate
                self._transition(task, AgentTaskState.normalize_output)
                self._transition(task, AgentTaskState.evaluate)
                evaluation = AgentCritic(self.model).evaluate(
                    effective_plan,
                    result.outputs,
                    iteration_id=iteration_id,
                )
                backend_checks = load_manifest(child_run_id).governance.validations
                for name, passed in backend_checks.items():
                    if name not in {"runtime_qualification", "recipe_committed", "agent_critic"}:
                        evaluation.hard_constraints[f"backend_{name}"] = passed
                candidate.evaluation = evaluation
                rank = (int(all(evaluation.hard_constraints.values())), evaluation.score)
                if rank > best_rank:
                    best_rank = rank
                    task.best_candidate = candidate
                task.budget_usage = ledger.settle(
                    cost_usd=result.actual_cost_usd + evaluation.cost_usd,
                    gpu_minutes=result.actual_gpu_minutes,
                )
                iteration.usage = {
                    "generation": result.usage,
                    "critic": evaluation.usage,
                }
                self._update_child_evaluation(child_run_id, evaluation, task)
                if evaluation.accepted:
                    task.final_candidate = candidate
                    task.stop_reason = "accepted"
                    self._transition(task, AgentTaskState.accepted)
                    break
                if index >= task.plan.envelope.budget.max_revisions:
                    task.stop_reason = "revision_limit"
                    break
                if not evaluation.revision:
                    task.stop_reason = "no_safe_revision"
                    break
                current_parameters = validate_revision(effective_plan, evaluation.revision)
                self._transition(task, AgentTaskState.revise)
                self._transition(task, AgentTaskState.execute)
            except Exception as exc:
                if task.best_candidate is None and iteration.candidate is not None:
                    task.best_candidate = iteration.candidate
                if ledger.usage.reserved_cost_usd or ledger.usage.reserved_gpu_minutes:
                    ledger.abandon()
                ledger.release()
                task.budget_usage = ledger.usage
                iteration.error = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "details": getattr(exc, "details", {}),
                }
                terminal_error = exc
                detail = str(exc) + str(getattr(exc, "details", {}))
                if "moderation" in detail.lower():
                    task.stop_reason = "provider_moderation"
                elif isinstance(exc, PolicyError) and "budget" in detail.lower():
                    task.stop_reason = "budget_exhausted"
                else:
                    task.stop_reason = "unrecoverable_error"
                break
            finally:
                task.updated_at = datetime.now(UTC)
                self.store.save_task(task)
        if task.final_candidate is None:
            task.final_candidate = task.best_candidate
        self._transition(task, AgentTaskState.finalize)
        self._finalize_parent(parent.run_id, task, terminal_error)
        session = self.store.load_session(task.session_id)
        session.turns.append(
            AgentTurn(
                role="assistant",
                content=f"Task {task.id} finalized: {task.stop_reason}",
                task_id=task.id,
            )
        )
        session.updated_at = datetime.now(UTC)
        self.store.save_session(session)
        return json.loads(task.model_dump_json())

    def reject(self, task_id: str) -> dict[str, Any]:
        task = self.store.load_task(task_id)
        if task.state != AgentTaskState.awaiting_approval:
            raise PolicyError("Only an awaiting task can be rejected")
        task.stop_reason = "user_rejected"
        self._transition(task, AgentTaskState.rejected)
        self._transition(task, AgentTaskState.finalize)
        return json.loads(task.model_dump_json())

    def inspect(self, identifier: str) -> dict[str, Any]:
        try:
            return json.loads(self.store.load_task(identifier).model_dump_json())
        except ValidationError:
            return json.loads(self.store.load_session(identifier).model_dump_json())

    def _session(self, session_id: str | None) -> AgentSession:
        if session_id:
            return self.store.load_session(session_id)
        session = AgentSession(id=f"session-{uuid.uuid4().hex[:16]}")
        self.store.save_session(session)
        return session

    @staticmethod
    def _plan_dependencies(plan: GenerationPlan) -> dict[str, str]:
        if plan.envelope.allowed_tools[0] in {"sprites.build", "drama.render"}:
            return MediaToolBackend.dependencies(plan)
        paths = ["configs/providers/openai-pricing.yaml"]
        if plan.backend == "openai":
            paths.append("configs/providers/openai-models.yaml")
        elif plan.envelope.allowed_tools == ["comfy.generate"]:
            from ..workflows.compiler import load_recipe

            paths.extend([
                f"configs/workflows/recipes/{plan.recipe}.yaml",
                "configs/models/catalog.yaml", "configs/runtime/comfyui.lock.yaml",
            ])
            if (find_repo_root() / paths[-3]).is_file():
                paths.append(load_recipe(str(plan.recipe)).base_workflow)
        return {
            name: sha256_file(find_repo_root() / name)
            for name in paths if (find_repo_root() / name).is_file()
        }

    @staticmethod
    def _verify_plan_inputs(plan: GenerationPlan) -> None:
        for name, digest in plan.dependency_hashes.items():
            path = (find_repo_root() / name).resolve()
            if find_repo_root().resolve() not in path.parents:
                raise PolicyError("Plan dependency is outside the repository")
            verify_sha256(path, digest)
        for asset in plan.input_assets:
            verify_sha256(Path(asset.local_path), asset.sha256)
            verify_sha256(Path(asset.derived_path), asset.derived_sha256)

    def _inspect_capabilities(
        self,
        intent: GenerationIntent,
        capabilities: list[BackendCapability],
    ) -> list[BackendCapability]:
        if self._fixed_capabilities is not None:
            return capabilities
        result = [item.model_copy(deep=True) for item in capabilities]
        comfy = next(item for item in result if item.backend == "comfy")
        if intent in {GenerationIntent.sprite_sequence, GenerationIntent.short_drama}:
            return result
        recipe_id, model_id = self._local_selection(intent)
        try:
            ComfyClient(timeout=2).system_stats()
            model = load_catalog().by_id()[model_id]
            verified = ModelManager().verify_model(model)
            if not all(item["ok"] for item in verified):
                raise ReadinessError(f"missing model {model_id}; recipe {recipe_id}")
        except Exception as exc:
            comfy.available = False
            comfy.reason = str(exc)
        remote = next(item for item in result if item.backend == "openai")
        if not load_llm_api_key():
            remote.available = False
            remote.reason = "LLM_API_KEY is not configured"
        return result

    @staticmethod
    def _local_selection(intent: GenerationIntent) -> tuple[str, str]:
        selections = {
            GenerationIntent.text_to_image: ("sdxl-t2i", "sdxl-base-1.0"),
            GenerationIntent.image_to_image: ("sdxl-i2i", "sdxl-base-1.0"),
            GenerationIntent.text_to_video: ("wan21-t2v", "wan21-t2v-1.3b"),
            GenerationIntent.image_to_video: ("wan22-i2v-experimental", "wan22-ti2v-5b"),
            GenerationIntent.sprite_sequence: ("general-rgba-512", "wan21-t2v-1.3b"),
            GenerationIntent.short_drama: ("short-drama", "wan21-t2v-1.3b"),
        }
        return selections[intent]

    def _build_plan(
        self,
        *,
        task_id: str,
        session_id: str,
        user_intent: str,
        interpreted: dict[str, Any],
        intent: GenerationIntent,
        backend: str,
        assets: list,
        budget: TaskBudget,
    ) -> GenerationPlan:
        prompt = str(interpreted.get("prompt", user_intent))
        negative = str(interpreted.get("negative_prompt", "text, watermark, low quality"))
        criteria = list(interpreted.get("acceptance_criteria") or ["Matches the stated user intent"])
        requested = {key: value for key, value in (interpreted.get("parameters") or {}).items() if value is not None}
        if backend == "openai":
            size = requested.get("size", "1024x1024")
            quality = requested.get("quality", "low")
            background = requested.get("background", "auto")
            snapshot = image_model_snapshot()
            pricing = load_pricing()
            input_reserve = 0.08 * len(assets)
            reserve = 2 * image_output_reservation(pricing, size=size, quality=quality) + input_reserve + 0.06
            parameters = {
                "prompt": prompt,
                "size": size,
                "quality": quality,
                "background": background,
                "pricing_id": pricing.id,
            }
            return GenerationPlan(
                task_id=task_id,
                session_id=session_id,
                intent=intent,
                user_intent=user_intent,
                backend="openai",
                model=snapshot,
                parameters=parameters,
                input_assets=assets,
                acceptance_criteria=criteria,
                estimated_iteration_cost_usd=reserve,
                envelope=ExecutionEnvelope(
                    backend="openai",
                    model=snapshot,
                    max_width=int(size.split("x")[0]),
                    max_height=int(size.split("x")[1]),
                    quality=quality,
                    background=background,
                    allowed_tools=["openai.image"],
                    mutable_parameters=["prompt"],
                    budget=budget,
                ),
            )
        recipe, model = self._local_selection(intent)
        if intent in {GenerationIntent.text_to_image, GenerationIntent.image_to_image}:
            parameters = {"prompt": prompt, "negative_prompt": negative, "seed": 7, "steps": 25, "cfg": 6.5}
            if intent == GenerationIntent.text_to_image:
                parameters.update({"width": requested.get("width", 1024), "height": requested.get("height", 1024)})
            else:
                parameters["denoise"] = requested.get("denoise", 0.65)
            parameters["steps"] = requested.get("steps", 25)
            width, height = parameters.get("width", 1536), parameters.get("height", 1536)
            tool = "comfy.generate"
            gpu = 15.0
            mutables = ["prompt", "negative_prompt", "seed", "steps", "cfg"]
            if intent == GenerationIntent.image_to_image:
                mutables.append("denoise")
        elif intent in {GenerationIntent.text_to_video, GenerationIntent.image_to_video}:
            parameters = {
                "prompt": prompt,
                "negative_prompt": negative,
                "seed": 7,
                "width": 512,
                "height": 512,
                "frames": 17,
                "fps": 16,
                "steps": 30,
            }
            parameters.update({
                key: requested[key] for key in ("width", "height", "frames", "fps", "steps") if key in requested
            })
            width, height = parameters["width"], parameters["height"]
            tool = "comfy.generate"
            gpu = 15.0
            mutables = ["prompt", "negative_prompt", "seed", "steps"]
        elif intent == GenerationIntent.sprite_sequence:
            parameters = {key: value for key, value in requested.items() if key in {"source_run_id", "profile"}}
            width = height = 8192
            tool = "sprites.build"
            gpu = 0.0
            mutables = []
        else:
            parameters = {key: value for key, value in requested.items() if key in {"project", "resume"}}
            width, height = 1920, 1080
            tool = "drama.render"
            gpu = 0.0
            mutables = []
        return GenerationPlan(
            task_id=task_id,
            session_id=session_id,
            intent=intent,
            user_intent=user_intent,
            backend="comfy",
            model=model,
            recipe=recipe,
            parameters=parameters,
            input_assets=assets,
            acceptance_criteria=criteria,
            estimated_iteration_cost_usd=0.01,
            estimated_iteration_gpu_minutes=gpu,
            envelope=ExecutionEnvelope(
                backend="comfy",
                model=model,
                recipe=recipe,
                max_width=width,
                max_height=height,
                max_steps=parameters.get("steps"),
                allowed_tools=[tool],
                mutable_parameters=mutables,
                budget=budget,
            ),
        )

    @staticmethod
    def _validate_estimate(plan: GenerationPlan, *, spent_cost_usd: float = 0) -> None:
        budget = plan.envelope.budget
        if plan.estimated_iteration_cost_usd > budget.max_iteration_cost_usd:
            raise PolicyError("Planned per-iteration remote cost exceeds budget")
        if plan.estimated_iteration_gpu_minutes > budget.max_iteration_gpu_minutes:
            raise PolicyError("Planned per-iteration GPU time exceeds budget")
        count = budget.max_revisions + 1
        if spent_cost_usd + plan.estimated_iteration_cost_usd * count > budget.max_total_cost_usd:
            raise PolicyError("Worst-case planned remote cost exceeds total budget")
        if plan.estimated_iteration_gpu_minutes * count > budget.max_total_gpu_minutes:
            raise PolicyError("Worst-case planned GPU time exceeds total budget")

    def _backend_for_tool(self, tool_name: str) -> GenerationBackend:
        if tool_name in self.backends:
            return self.backends[tool_name]
        if tool_name == "openai.image":
            return OpenAIImageBackend()
        if tool_name == "comfy.generate":
            return ComfyBackend()
        if tool_name in {"sprites.build", "drama.render"}:
            return MediaToolBackend()
        raise PolicyError(f"Tool is not implemented: {tool_name}")

    def _create_parent_manifest(self, task: AgentTaskRecord):
        assert task.plan is not None and task.approval is not None
        lane = LicenseLane.production
        manifest = create_manifest(
            kind="agent_task",
            parameters={"intent": task.plan.user_intent, "plan": task.plan.model_dump(mode="json")},
            license_lanes=[lane],
            source={"input_sha256": [item.sha256 for item in task.plan.input_assets]},
            agent=AgentRunMetadata(
                session_id=task.session_id,
                task_id=task.id,
                approval_fingerprint=task.approval.plan_fingerprint,
                input_asset_sha256=[item.sha256 for item in task.plan.input_assets],
            ),
        )
        manifest.governance.validations.update({"approval": True, "budget": True})
        manifest.status = "running"
        mlflow_run_id = log_manifest(
            manifest.run_id,
            {"kind": "agent_task", "task_id": task.id, "backend": task.plan.backend},
            {"planning_cost_usd": task.budget_usage.actual_cost_usd},
        )
        if mlflow_run_id:
            manifest.tracking["mlflow_run_id"] = mlflow_run_id
        save_manifest(manifest)
        return manifest

    def _record_child(
        self,
        task: AgentTaskRecord,
        iteration: AgentIteration,
        result: GenerationResult,
        parent_run_id: str,
    ) -> str:
        assert task.plan is not None and task.approval is not None
        metadata = AgentRunMetadata(
            session_id=task.session_id,
            task_id=task.id,
            iteration_id=iteration.id,
            approval_fingerprint=task.approval.plan_fingerprint,
            provider_request_id=result.request_id,
            model_snapshot=result.model_snapshot,
            redacted_request_sha256=result.request_hash,
            input_asset_sha256=[item.sha256 for item in task.plan.input_assets],
            budget_usage=task.budget_usage.model_dump(mode="json"),
        )
        if result.run_id:
            manifest = load_manifest(result.run_id)
            if manifest.parent_run_id and manifest.parent_run_id != parent_run_id:
                manifest.source["derived_from_run_id"] = manifest.parent_run_id
            manifest.parent_run_id = parent_run_id
            manifest.agent = metadata
            parent_mlflow_id = load_manifest(parent_run_id).tracking.get("mlflow_run_id")
            child_mlflow_id = log_manifest(
                manifest.run_id,
                {"kind": manifest.kind, "task_id": task.id, "iteration": iteration.id},
                {
                    "cost_usd": result.actual_cost_usd,
                    "gpu_minutes": result.actual_gpu_minutes,
                },
                artifacts=[Path(item.path) for item in manifest.outputs],
                parent_run_id=parent_mlflow_id,
            )
            if child_mlflow_id:
                manifest.tracking["agent_mlflow_run_id"] = child_mlflow_id
            save_manifest(manifest)
            return manifest.run_id
        manifest = create_manifest(
            kind="remote_image_generation",
            parameters=iteration.parameters,
            license_lanes=[LicenseLane.production],
            source={"provider": "openai", "model_snapshot": result.model_snapshot},
            parent_run_id=parent_run_id,
            agent=metadata,
        )
        for path in result.outputs:
            add_output(manifest, path, role="primary_image", media_kind="image")
        manifest.tracking.update({"provider_request_id": result.request_id, "usage": result.usage})
        manifest.governance.validations.update({"contract": True, "hashes": True, "provenance": True})
        manifest.status = "succeeded"
        parent_mlflow_id = load_manifest(parent_run_id).tracking.get("mlflow_run_id")
        child_mlflow_id = log_manifest(
            manifest.run_id,
            {"kind": manifest.kind, "task_id": task.id, "iteration": iteration.id},
            {"cost_usd": result.actual_cost_usd},
            artifacts=result.outputs,
            parent_run_id=parent_mlflow_id,
        )
        if child_mlflow_id:
            manifest.tracking["mlflow_run_id"] = child_mlflow_id
        save_manifest(manifest)
        return manifest.run_id

    @staticmethod
    def _update_child_evaluation(run_id: str, evaluation, task: AgentTaskRecord) -> None:
        manifest = load_manifest(run_id)
        if manifest.agent:
            manifest.agent.critic = evaluation.model_dump(mode="json")
            manifest.agent.budget_usage = task.budget_usage.model_dump(mode="json")
        manifest.governance.validations["agent_critic"] = evaluation.accepted
        save_manifest(manifest)

    @staticmethod
    def _finalize_parent(run_id: str, task: AgentTaskRecord, error: Exception | None) -> None:
        manifest = load_manifest(run_id)
        if task.final_candidate:
            for path, digest in zip(task.final_candidate.outputs, task.final_candidate.output_hashes, strict=True):
                add_output(
                    manifest,
                    Path(path),
                    role="selected_candidate",
                    derived_from_run_id=task.final_candidate.run_id,
                    derived_from_sha256=digest,
                )
        successful = task.stop_reason in {"accepted", "revision_limit"} and task.final_candidate
        manifest.status = "succeeded" if successful else "failed"
        if manifest.agent:
            manifest.agent.budget_usage = task.budget_usage.model_dump(mode="json")
            manifest.agent.stop_reason = task.stop_reason
            if task.final_candidate and task.final_candidate.evaluation:
                manifest.agent.critic = task.final_candidate.evaluation.model_dump(mode="json")
        manifest.governance.validations["agent_critic"] = bool(
            task.final_candidate and task.final_candidate.evaluation and task.final_candidate.evaluation.accepted
        )
        if task.final_candidate and task.final_candidate.run_id:
            selected = load_manifest(task.final_candidate.run_id)
            manifest.governance.license_lanes = selected.governance.license_lanes
            for name, passed in selected.governance.validations.items():
                manifest.governance.validations[f"selected_{name}"] = passed
        manifest.governance.validations["contract"] = True
        manifest.governance.validations["hashes"] = bool(task.final_candidate)
        if error:
            manifest.error = {"type": type(error).__name__, "message": str(error)}
        parent_mlflow_id = manifest.tracking.get("mlflow_run_id")
        tracked = log_manifest(
            manifest.run_id,
            {"status": manifest.status, "stop_reason": task.stop_reason or "unknown"},
            {
                "iteration_count": float(len(task.iterations)),
                "actual_cost_usd": task.budget_usage.actual_cost_usd,
                "actual_gpu_minutes": task.budget_usage.actual_gpu_minutes,
            },
            artifacts=[Path(item.path) for item in manifest.outputs],
            existing_run_id=parent_mlflow_id,
        )
        if tracked:
            manifest.tracking["mlflow_run_id"] = tracked
        save_manifest(manifest)

    def _transition(self, task: AgentTaskRecord, state: AgentTaskState) -> None:
        if state not in ALLOWED_TRANSITIONS[task.state]:
            raise PolicyError(f"Invalid Agent state transition: {task.state.value} -> {state.value}")
        task.state = state
        task.state_history.append(state)
        task.updated_at = datetime.now(UTC)
        self.store.save_task(task)


def load_budget(path: Path) -> TaskBudget:
    return load_typed(path, TaskBudget)
