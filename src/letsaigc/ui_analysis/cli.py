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
from ..schemas.ui_provider import ManualUIInput, UIInputManifest, UISource
from ..ui_providers.intake import UIIntake
from . import selection as selection_tools

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


def _trusted_sources(instance, request, *, model_bindings=None):
    """Return source IDs and original refs from the frozen task input."""
    inputs = getattr(request, "input", request)
    if inputs.kind != "manual":
        raise PipelineError("selection_not_ready")
    if inputs.metadata_ref:
        manifest = UIInputManifest.model_validate_json(instance.artifacts.read(inputs.metadata_ref))
        if sorted(source.original_ref.model_dump_json() for source in manifest.sources) != sorted(
            ref.model_dump_json() for ref in inputs.inputs
        ):
            raise PipelineError("source_scope")
        sources = {
            source.source_id: selection_tools.TrustedSource(source.source_id, source.original_ref)
            for source in manifest.sources
        }
    else:
        sources = {
            selection_tools.source_id_for_ref(ref, index): selection_tools.TrustedSource(
                selection_tools.source_id_for_ref(ref, index), ref
            )
            for index, ref in enumerate(inputs.inputs, 1)
        }
    bindings = model_bindings if model_bindings is not None else getattr(request, "model_bindings", {})
    if "layout_ref" in bindings:
        layout = json.loads(instance.artifacts.read(bindings["layout_ref"]))
        if len(sources) != 1 or layout["source_id"] not in sources:
            raise PipelineError("source_scope")
        sources = {
            identity: selection_tools.TrustedSource(
                identity, source.original_ref, layout["width"], layout["height"],
                bindings["layout_ref"], bindings["canonical_ref"],
            ) for identity, source in sources.items()
        }
    return sources


def _check_selection_mode(value, mode):
    if mode == "decompose" and any(source.keep_elements or source.remove_elements for source in value.sources):
        raise PipelineError("invalid_selection")


def _reviewed_input(instance, task_id, reviewed_task, review_revision):
    binding = selection_tools.reviewed_binding(instance, reviewed_task, review_revision)
    binding_data, refs = selection_tools.rebind_reviewed_layout(instance, binding, task_id)
    original_ref = instance.artifacts.put(
        task_id,
        "review-input",
        instance.artifacts.read(refs["canonical_ref"]),
        role="original",
        media_type=refs["canonical_ref"].media_type,
        source_ids=[refs["canonical_ref"].artifact_id],
    )
    layout_payload = json.loads(instance.artifacts.read(refs["layout_ref"]))
    source_id = layout_payload.get("source_id")
    if not isinstance(source_id, str):
        source_id = "source-" + refs["canonical_ref"].sha256[:24] + "-1"
    provenance_ref = instance.artifacts.put(
        task_id,
        "review-input",
        json.dumps(
            {
                "schema_version": 1,
                "origin": "reviewed",
                "acquisition_method": "review_binding",
                "observed": {"sha256": original_ref.sha256},
                "reviewed_task": reviewed_task,
                "review_revision": binding.review_revision,
                "confirmed_binding": binding.model_dump(mode="json"),
                "license_status": "unknown",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
        role="provenance",
        source_ids=[original_ref.artifact_id],
    )
    source = UISource(
        source_id=source_id,
        original_ref=original_ref,
        provenance_ref=provenance_ref,
        input_entry_ids=["input-1"],
    )
    manifest = UIInputManifest(
        task_id=task_id,
        status="ready",
        entries=[{"entry_id": "input-1", "status": "ready", "source_id": source_id}],
        sources=[source],
    )
    metadata_ref = instance.artifacts.put(
        task_id,
        "review-input",
        json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode(),
        role="input_manifest",
        source_ids=[original_ref.artifact_id, provenance_ref.artifact_id],
    )
    request_input = ManualUIInput(inputs=[original_ref], metadata_ref=metadata_ref)
    return request_input, binding_data, refs


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
    reviewed_task: str | None = typer.Option(None, "--reviewed-task"),
    automatic_task: str | None = typer.Option(None, "--automatic-task"),
    review_revision: int | None = typer.Option(None, "--review-revision", min=0),
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
    if reviewed_task is None and review_revision is not None:
        raise PipelineError("invalid_input")
    if (reviewed_task or automatic_task) and max_images is not None:
        raise PipelineError("invalid_input")
    if reviewed_task and selection:
        raise PipelineError("invalid_selection")
    if sum(bool(value) for value in (input_manifest, query, reviewed_task, automatic_task)) != 1:
        raise PipelineError("invalid_input")
    if max_images is not None and max_images > 1:
        raise PipelineError("capability_not_ready")
    if mode == "parse" and (
        target or selection or text_assets or remove_text or allow_local_revision or reviewed_task or automatic_task
    ):
        raise PipelineError("capability_not_ready")
    if mode == "decompose" and (target or remove_text):
        raise PipelineError("invalid_input")
    if mode == "reconstruct" and target not in {"scene_background", "map_surface"}:
        raise PipelineError("invalid_input")
    limits = UIPolicy.model_validate(load_document(policy or find_repo_root() / "configs/ui-analysis/default.yaml"))
    task_budget = TaskBudget.model_validate(load_document(budget))
    if task_budget.max_revisions > 2:
        raise PipelineError("invalid_budget")
    instance = service()
    task_id = "ui-" + uuid4().hex
    from .runtime import freeze_models, freeze_search

    acquisition = {"kind": "manual"}
    review_refs = {}
    review_info = None
    automatic_info = None
    if reviewed_task:
        inputs, binding_data, review_refs = _reviewed_input(instance, task_id, reviewed_task, review_revision)
        review_info = {
            "task_id": reviewed_task,
            "review_revision": binding_data["review_revision"],
            "binding": binding_data,
            "model_calls": 0,
        }
        acquisition = {"kind": "reviewed", "task_id": reviewed_task, "review_revision": binding_data["review_revision"]}
    elif automatic_task:
        from .edit_inputs import automatic_input

        inputs, automatic_info, review_refs = automatic_input(instance, task_id, automatic_task)
        acquisition = {"kind": "automatic", "task_id": automatic_task}
    elif query:
        with instance.ledger.transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {3, 4, 5}:
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

    model_bindings = {} if review_refs else freeze_models(instance.artifacts, task_id)
    model_bindings.update(review_refs)
    selection_ref = None
    selection_info = {"mode": "none", "state": "not_applicable", "model_calls": 0}
    if mode != "parse":
        selection_info.update(mode="deferred", state="awaiting_selection")
    if selection:
        if mode == "parse":
            raise PipelineError("invalid_selection")
        source_map = _trusted_sources(instance, inputs, model_bindings=review_refs)
        parsed = selection_tools.validate_selection(
            instance.artifacts,
            task_id,
            selection_tools.load_document(selection),
            source_map,
        )
        _check_selection_mode(parsed, mode)
        selection_ref = selection_tools.register_selection(
            instance.artifacts, task_id, parsed, source_map,
            operation_id="selection-input",
        ).selection_ref
        selection_info = {
            "mode": "bound",
            "state": "awaiting_approval",
            "selection_ref": selection_ref,
            "model_calls": 0,
        }
    request = UIAnalysisRequest(
        input=inputs,
        output_mode=mode,
        language=language,
        reconstruction_target=target,
        selection_mode="bound" if selection_ref else "deferred" if mode != "parse" else "none",
        selection_ref=selection_ref,
        text_assets=text_assets,
        remove_text=remove_text,
        allow_local_revision=allow_local_revision,
        budget=task_budget,
        resources=limits.resources,
        limits=limits.limits,
        model_bindings=model_bindings,
    )
    result = instance.ui_plan(task_id, request)
    # Confirmed inputs produce deterministic proposals from their frozen layout.
    if selection_ref:
        selection_tools.record_selection(instance.artifacts, task_id, selection_ref)
    if mode != "parse" and review_refs and not selection_ref:
        from ..agent.ui_analyzer import UIAnalyzer

        source_map = _trusted_sources(instance, request)
        proposals = UIAnalyzer.propose_selection(
            instance.artifacts, task_id, next(iter(source_map.values())),
            review_refs["layout_ref"], output_mode=mode, target=target, remove_text=remove_text,
        )
        catalog = selection_tools.selection_catalog(instance.artifacts, task_id)
        selection_info = {
            "mode": "deferred",
            "state": catalog.state if catalog else "awaiting_selection",
            "candidates": [item.candidate for item in proposals],
            "model_calls": 0,
            "requirements": [] if proposals else ["Choose bounded regions from the frozen layout"],
        }
    emit(
        "plan",
        "planned",
        task_id=task_id,
        plan_fingerprint=result.fingerprint,
        workflow_type=result.workflow_type,
        output_mode=mode,
        allowed_capabilities=result.envelope.allowed_capabilities,
        budget=task_budget,
        estimated_usage={"planning_cost_usd": 0, "analysis_reservation_usd": task_budget.max_iteration_cost_usd},
        inputs=result.inputs,
        quality_status="pending",
        acquisition=acquisition,
        selection=selection_info,
        reviewed=review_info,
        automatic=automatic_info,
        execution_capability="not_ready" if mode != "parse" else "parse",
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


@ui_app.command("select")
@guarded("select")
def select(
    task_id: str,
    candidate: str | None = typer.Option(None, "--candidate"),
    selection: Path | None = typer.Option(None, "--selection"),
):
    """Record a trusted region choice without calling a model or provider."""
    if (candidate is None) == (selection is None):
        raise PipelineError("invalid_selection", "Pass exactly one of --candidate or --selection")
    instance = service()
    plan = checked_ui(instance, task_id, verify=False)
    request = UIAnalysisRequest.model_validate_json(
        instance.artifacts.read(ArtifactRef.model_validate(plan.parameters["request_ref"]))
    )
    if request.output_mode == "parse" or request.selection_mode != "deferred":
        raise PipelineError("invalid_selection", "Selection is available only for deferred editing plans")
    if candidate is not None:
        proposed = selection_tools.find_candidate(instance.artifacts, task_id, candidate)
        from ..schemas.ui import UISelection

        _check_selection_mode(
            UISelection.model_validate_json(instance.artifacts.read(proposed.selection_ref)), request.output_mode,
        )
        prepared = selection_tools.select_candidate(instance.artifacts, task_id, candidate)
        candidate_info = {"candidate_id": candidate}
    else:
        source_map = _trusted_sources(instance, request)
        parsed = selection_tools.validate_selection(
            instance.artifacts,
            task_id,
            selection_tools.load_document(selection),
            source_map,
        )
        _check_selection_mode(parsed, request.output_mode)
        prepared = selection_tools.register_selection(
            instance.artifacts, task_id, parsed, source_map,
            operation_id="selection-select",
        )
        catalog = selection_tools.record_selection(instance.artifacts, task_id, prepared.selection_ref)
        candidate_info = {}
    if candidate is not None:
        catalog = selection_tools.selection_catalog(instance.artifacts, task_id)
    # This command intentionally does not call approve(), reserve(),
    # submit_step(), or any model adapter.  The later child workflow owns
    # approval and GPU execution.
    emit(
        "select",
        "selection_recorded",
        task_id=task_id,
        selection_ref=prepared.selection_ref,
        selection_hash=prepared.selection_ref.sha256,
        selection_revision=catalog.revision,
        model_calls=0,
        gpu_calls=0,
        candidate=candidate_info,
        pending_approval={
            "state": catalog.state,
            "selection_ref": prepared.selection_ref,
            "selection_hash": prepared.selection_ref.sha256,
        },
    )


@ui_app.command("execute")
@guarded("execute")
def execute(task_id: str, fingerprint: str = typer.Option(..., "--approve")):
    instance = service()
    plan = checked_ui(instance, task_id, fingerprint)
    request = UIAnalysisRequest.model_validate_json(
        instance.artifacts.read(ArtifactRef.model_validate(plan.parameters["request_ref"]))
    )
    # The legacy UI workflow is parse-only.  Keep this gate here until the
    # T028 workflow wires reviewed/decompose/reconstruct plans; in particular,
    # do not consume an analysis approval and then accidentally rerun OCR/VLM.
    if request.output_mode != "parse":
        raise PipelineError("capability_not_ready")
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
    request = UIAnalysisRequest.model_validate_json(
        instance.artifacts.read(ArtifactRef.model_validate(plan.parameters["request_ref"]))
    )
    editing = request.output_mode != "parse"
    current = (
        local_projection(instance, task_id)
        if local or editing else asyncio.run(temporal.inspect(task_id, load_config()))
    )
    current = current.model_dump(mode="json") if isinstance(current, PipelineRun) else current
    operations = instance.ledger.list_operations(task_id)
    steps = current.get("steps", []) if current else []
    phase = steps[-1]["step_id"] if steps else "planning"
    selection_catalog = selection_tools.selection_catalog(instance.artifacts, task_id)
    selection_view = None
    if selection_catalog is not None:
        selection_view = {
            "task_id": selection_catalog.task_id,
            "revision": selection_catalog.revision,
            "state": selection_catalog.state,
            "candidates": selection_catalog.candidates,
            "selection_ref": selection_catalog.selection_ref,
            "model_calls": selection_catalog.model_calls,
        }
    emit(
        "inspect",
        current["state"] if current else "planned",
        task_id=task_id,
        plan_fingerprint=plan.fingerprint,
        task=current,
        phase=phase,
        source="local_projection" if local or editing else "live",
        stale=local or editing,
        root_budget_id=task_id,
        budget=plan.envelope.budget,
        usage=instance.ledger.usage(task_id),
        pending_approvals=(
            [{"task_id": task_id, "plan_fingerprint": plan.fingerprint}]
            if not editing and (current is None or current.get("state") == "awaiting_approval")
            else []
        ),
        selection=selection_view,
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


@ui_app.command('review')
@guarded('ui.review')
def review_command(task_id: str, open_browser: bool = typer.Option(True, '--open/--no-open')):
    """Open a local human correction session for a completed parse."""
    import webbrowser

    from .review import open_review
    from .review_server import ReviewServer

    current = service()
    repo = open_review(current, task_id)
    server = ReviewServer(repo, task_id)
    emit('ui.review', 'ready', task_id=task_id, url=server.session.origin,
         session='browser_bootstrap' if open_browser else 'not_exchanged', external_calls=0)
    try:
        if open_browser:
            webbrowser.open(server.session.origin + '/#' + server.session.bootstrap)
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
