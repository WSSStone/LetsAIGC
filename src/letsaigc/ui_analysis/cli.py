"""Trusted local UI entrypoint. Planning records inputs and never calls a model."""

import asyncio
import json
from functools import wraps
from pathlib import Path
from uuid import uuid4

import typer
import yaml

from ..errors import LetsAIGCError
from ..execution.temporal import client as temporal
from ..execution.temporal.config import load_config, runtime_root
from ..execution.temporal.projection import local_projection
from ..paths import find_repo_root
from ..pipelines.approval import approve
from ..pipelines.errors import PipelineError
from ..pipelines.service import PipelineService
from ..schemas import TaskBudget
from ..schemas.pipeline import ArtifactRef, PipelineRun
from ..schemas.ui import UIAnalysisRequest, UIPolicy
from ..schemas.ui_provider import UIInputManifest
from ..ui_providers.intake import UIIntake

ui_app = typer.Typer(no_args_is_help=True, help="Import, plan, approve and inspect game UI analysis")


def service():
    return PipelineService(runtime_root(), ui_schema=3)


def emit(command, status, **values):
    def serialize(value):
        return value.model_dump(mode="json") if hasattr(value, "model_dump") else value

    typer.echo(
        json.dumps(
            {"schema_version": 1, "command": command, "status": status, **values},
            default=serialize,
            ensure_ascii=False,
            indent=2,
        )
    )


def guarded(command):
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except typer.Exit:
                raise
            except Exception as exc:
                code = (
                    exc.code
                    if isinstance(exc, PipelineError)
                    else "invalid_input"
                    if isinstance(exc, (ValueError, OSError, LetsAIGCError))
                    else "execution_unknown"
                )
                exit_code = 2
                if code in {
                    "plan_changed",
                    "dependency_changed",
                    "artifact_scope",
                    "artifact_hash",
                    "approval_mismatch",
                    "untrusted_approval",
                    "budget_insufficient",
                    "not_approved",
                    "call_limit",
                    "input_changed",
                    "artifact_changed",
                    "approval_required",
                    "budget_exceeded",
                }:
                    exit_code = 4
                elif code.endswith("not_ready") or code in {
                    "temporal_unavailable",
                    "resource_insufficient",
                    "migration_required",
                }:
                    exit_code = 3
                elif code in {
                    "execution_unknown",
                    "outcome_unknown",
                    "recovery_unavailable",
                    "vision_unavailable",
                    "input_resupply_required",
                    "search_unavailable",
                }:
                    exit_code = 5
                emit(command, "error", error={"code": code, "safe_message": code.replace("_", " ")})
                raise typer.Exit(exit_code) from None

        return invoke

    return decorate


def load_document(path):
    with Path(path).open("rb") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise PipelineError("invalid_input")
    return yaml.safe_load(content)


def load_ref(path):
    return ArtifactRef.model_validate(load_document(path))


@ui_app.command("import")
@guarded("import")
def import_images(
    image: list[str] = typer.Option(None, "--image"),
    artifact: list[Path] = typer.Option(None, "--artifact"),
    metadata: Path | None = typer.Option(None, "--metadata"),
):
    if bool(image) == bool(artifact):
        raise PipelineError("invalid_input")
    instance = service()
    values = image or [load_ref(path) for path in artifact]
    scopes = {item.task_id for item in values if isinstance(item, ArtifactRef)}
    ref = UIIntake(instance.artifacts).import_images(
        values, user_declared=load_document(metadata) if metadata else None, allowed_scopes=scopes
    )
    result = UIInputManifest.model_validate_json(instance.artifacts.read(ref))
    emit(
        "import",
        result.status,
        input_manifest_ref=ref,
        input_count=len(result.entries),
        source_count=len(result.sources),
        entries=result.entries,
    )
    if result.status == "unavailable":
        raise typer.Exit(2)


@ui_app.command("plan")
@guarded("plan")
def plan(
    input_manifest: Path | None = typer.Option(None, "--input-manifest"),
    query: str | None = typer.Option(None, "--query"),
    budget: Path = typer.Option(..., "--budget"),
    max_images: int | None = typer.Option(None, "--max-images", min=1, max=10),
    mode: str = typer.Option("parse", "--mode"),
    target: str | None = typer.Option(None, "--target"),
    selection: Path | None = typer.Option(None, "--selection"),
    policy: Path | None = typer.Option(None, "--policy"),
    language: str = typer.Option("auto", "--language"),
    text_assets: bool = typer.Option(False, "--text-assets"),
    remove_text: bool = typer.Option(False, "--remove-text"),
    allow_local_revision: bool = typer.Option(False, "--allow-local-revision"),
):
    if mode not in {"parse", "decompose", "reconstruct"} or language not in {"auto", "zh", "en"}:
        raise PipelineError("invalid_input")
    if mode != "parse" or target or selection or text_assets or remove_text or allow_local_revision:
        raise PipelineError("capability_not_ready")
    if bool(input_manifest) == bool(query) or (input_manifest and max_images is not None):
        raise PipelineError("invalid_input")
    if max_images is not None and max_images > 1:
        raise PipelineError("capability_not_ready")
    limits = UIPolicy.model_validate(load_document(policy or find_repo_root() / "configs/ui-analysis/default.yaml"))
    task_budget = TaskBudget.model_validate(load_document(budget))
    if task_budget.max_revisions > 2:
        raise PipelineError("invalid_budget")
    instance = service()
    task_id = "ui-" + uuid4().hex
    from .runtime import freeze_models, freeze_search

    acquisition = {"kind": "manual"}
    if query:
        with instance.ledger.transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != 3:
                raise PipelineError("migration_required")
        inputs = freeze_search(instance.artifacts, task_id, query)
        routing = json.loads(instance.artifacts.read(inputs.routing_policy_ref))["routing"]
        acquisition = {
            "kind": "search",
            "allowed_providers": routing["allowed_providers"],
            "serpapi_no_cache": routing["serpapi_no_cache"],
            "max_images": 1,
        }
    else:
        ref = load_ref(input_manifest)
        manifest = UIInputManifest.model_validate_json(instance.artifacts.read(ref))
        if len(manifest.entries) != 1:
            raise PipelineError("capability_not_ready")
        inputs = UIIntake(instance.artifacts).rebind(ref, task_id, allowed_scopes={ref.task_id})

    request = UIAnalysisRequest(
        input=inputs,
        language=language,
        budget=task_budget,
        resources=limits.resources,
        limits=limits.limits,
        model_bindings=freeze_models(instance.artifacts, task_id),
    )
    result = instance.ui_plan(task_id, request)
    emit(
        "plan",
        "planned",
        task_id=task_id,
        plan_fingerprint=result.fingerprint,
        workflow_type=result.workflow_type,
        output_mode="parse",
        allowed_capabilities=result.envelope.allowed_capabilities,
        budget=task_budget,
        estimated_usage={"planning_cost_usd": 0, "analysis_reservation_usd": task_budget.max_iteration_cost_usd},
        inputs=result.inputs,
        quality_status="pending",
        acquisition=acquisition,
    )


async def start_approved(instance, plan, request):
    from temporalio.common import WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError

    from ..execution.temporal.ui_messages import UIWorkflowInput

    config = load_config()
    client = await temporal.connect(config)
    try:
        handle = await client.start_workflow(
            "letsaigc.ui.analysis.v1",
            UIWorkflowInput(
                plan=plan,
                initial_approval=request,
                poll_seconds=config.poll_seconds,
                observations_per_run=config.observations_per_run,
                activity_timeout_seconds=config.activity_timeout_seconds,
            ),
            id=plan.task_id,
            task_queue=config.task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            result_type=PipelineRun,
        )
    except WorkflowAlreadyStartedError:
        handle = client.get_workflow_handle(plan.task_id)
        current = await handle.query("state", result_type=PipelineRun)
        if current and current.state.value == "awaiting_approval":
            await handle.execute_update("approval", request, id=request.request_id, result_type=str)
        elif current and current.state.value in {"failed", "rejected", "cancelled"}:
            raise PipelineError("recovery_unavailable") from None
    return handle.id


def checked_ui(instance, task_id, fingerprint=None, *, verify=True):
    plan = instance.ledger.plan(task_id)
    if plan.workflow_type != "ui_analysis":
        raise PipelineError("invalid_plan")
    return instance.checked_plan(task_id, fingerprint or plan.fingerprint, verify_inputs=verify)


@ui_app.command("execute")
@guarded("execute")
def execute(task_id: str, fingerprint: str = typer.Option(..., "--approve")):
    instance = service()
    plan = checked_ui(instance, task_id, fingerprint)
    from .runtime import preflight

    preflight(instance, plan)
    receipt = approve(instance.ledger, task_id, fingerprint)
    asyncio.run(start_approved(instance, plan, receipt))
    emit("execute", "accepted", task_id=task_id, plan_fingerprint=plan.fingerprint)


@ui_app.command("inspect")
@guarded("inspect")
def inspect(task_id: str, local: bool = typer.Option(False, "--local")):
    instance = service()
    plan = checked_ui(instance, task_id, verify=False)
    current = local_projection(instance, task_id) if local else asyncio.run(temporal.inspect(task_id, load_config()))
    current = current.model_dump(mode="json") if isinstance(current, PipelineRun) else current
    operations = instance.ledger.list_operations(task_id)
    steps = current.get("steps", []) if current else []
    phase = steps[-1]["step_id"] if steps else "planning"
    emit(
        "inspect",
        current["state"] if current else "planned",
        task_id=task_id,
        plan_fingerprint=plan.fingerprint,
        task=current,
        phase=phase,
        source="local_projection" if local else "live",
        stale=local,
        root_budget_id=task_id,
        budget=plan.envelope.budget,
        usage=instance.ledger.usage(task_id),
        pending_approvals=(
            [{"task_id": task_id, "plan_fingerprint": plan.fingerprint}]
            if current is None or current.get("state") == "awaiting_approval"
            else []
        ),
        children=[],
        quality_status="pending",
        usage_verdict=next(
            (op.result.get("usage_verdict") for op in reversed(operations) if op.result.get("usage_verdict")), None
        ),
    )


@ui_app.command("resume")
@guarded("resume")
def resume(task_id: str):
    instance = service()
    checked_ui(instance, task_id)
    current = asyncio.run(temporal.inspect(task_id, load_config()))
    if current is None or current.state.value in {"failed", "rejected", "cancelled", "awaiting_approval"}:
        raise PipelineError("recovery_unavailable")
    if current.state.value == "awaiting_reconciliation":
        asyncio.run(temporal.reconcile(task_id, load_config()))
    emit("resume", "accepted", task_id=task_id)


@ui_app.command("cancel")
@guarded("cancel")
def cancel(task_id: str):
    instance = service()
    checked_ui(instance, task_id, verify=False)
    asyncio.run(temporal.cancel(instance, task_id, load_config()))
    emit("cancel", "cancel_requested", task_id=task_id)


@ui_app.command("reconcile")
@guarded("reconcile")
def reconcile(task_id: str):
    checked_ui(service(), task_id, verify=False)
    asyncio.run(temporal.reconcile(task_id, load_config()))
    emit("reconcile", "reconciliation_requested", task_id=task_id)


@ui_app.command("doctor")
@guarded("doctor")
def doctor():
    from .runtime import diagnose

    emit("doctor", "checked", capabilities=diagnose())
