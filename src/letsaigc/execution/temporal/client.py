"""Expert local entrypoint. Importing this module does not require the SDK."""

from __future__ import annotations

from ...pipelines.approval import approve
from ...pipelines.errors import PipelineError
from ...pipelines.registry import WORKFLOWS
from ...pipelines.service import PipelineService
from ...pipelines.ui_children import is_child_plan
from ...schemas.pipeline import ArtifactRef, PipelinePlan, PipelineRun
from ...schemas.ui import UIAnalysisRequest
from .config import TemporalConfig, require_sdk
from .messages import WorkflowInput


def _ui_request(service: PipelineService, plan: PipelinePlan) -> UIAnalysisRequest:
    """Read the immutable UI request needed to choose the workflow envelope."""
    try:
        ref = ArtifactRef.model_validate(plan.parameters["request_ref"])
        return UIAnalysisRequest.model_validate_json(service.artifacts.read(ref))
    except Exception as exc:
        raise PipelineError("invalid_plan", "The UI request is unavailable") from exc


def _ledger_version(service: PipelineService) -> int:
    with service.ledger.transaction() as db:
        return int(db.execute("PRAGMA user_version").fetchone()[0])


def _require_editing_migration(service: PipelineService) -> None:
    if _ledger_version(service) < 5:
        raise PipelineError("migration_required", "Migrate the UI ledger to v5 before editing execution")


def _uses_frozen_editing_inputs(service: PipelineService, plan: PipelinePlan, request: UIAnalysisRequest) -> bool:
    """Whether the root can enter the child phase without running analysis."""
    if request.selection_ref is not None or "layout_ref" in request.model_bindings:
        return True
    from ...ui_analysis.selection import selection_catalog

    catalog = selection_catalog(service.artifacts, plan.task_id)
    return bool(catalog and (catalog.selection_ref is not None or len(catalog.candidates) == 1))


def _editing_workflow_input(service: PipelineService, plan: PipelinePlan, config: TemporalConfig):
    """Build the root-owned editing input while keeping child approval separate."""
    request = _ui_request(service, plan)
    _require_editing_migration(service)
    from .ui_messages import UIEditingWorkflowInput

    frozen = _uses_frozen_editing_inputs(service, plan, request)
    return (
        "letsaigc.ui.editing.v1",
        UIEditingWorkflowInput(
            plan=plan,
            phase_index=7 if frozen else 0,
            approved=frozen,
            poll_seconds=config.poll_seconds,
            observations_per_run=config.observations_per_run,
            activity_timeout_seconds=config.activity_timeout_seconds,
            active_limit_seconds=request.resources.active_seconds,
        ),
    )


async def connect(config: TemporalConfig):
    require_sdk()
    from temporalio.client import Client
    from temporalio.contrib.pydantic import pydantic_data_converter

    return await Client.connect(
        config.address,
        namespace=config.namespace,
        data_converter=pydantic_data_converter,
    )


async def start(service: PipelineService, task_id: str, config: TemporalConfig):
    from temporalio.common import WorkflowIDReusePolicy

    from .ui_messages import UIWorkflowInput

    plan = service.ledger.plan(task_id)
    if is_child_plan(plan):
        root_task_id = plan.parameters.get("root_task_id")
        raise PipelineError(
            "child_workflow_root_required",
            f"Start the root UI task {root_task_id!s}; editing children are not independent workflows",
        )
    workflow_name = WORKFLOWS[plan.workflow_type][0]
    argument = WorkflowInput(
        plan=plan,
        poll_seconds=config.poll_seconds,
        observations_per_run=config.observations_per_run,
        activity_timeout_seconds=config.activity_timeout_seconds,
    )
    if plan.workflow_type == "ui_analysis":
        request = _ui_request(service, plan)
        if request.output_mode == "parse":
            service.checked_plan(task_id, plan.fingerprint)
            argument = UIWorkflowInput(
                plan=plan,
                poll_seconds=config.poll_seconds,
                observations_per_run=config.observations_per_run,
                activity_timeout_seconds=config.activity_timeout_seconds,
                active_limit_seconds=request.resources.active_seconds,
            )
        else:
            _require_editing_migration(service)
            service.checked_plan(task_id, plan.fingerprint)
            workflow_name, argument = _editing_workflow_input(service, plan, config)
    else:
        service.checked_plan(task_id, plan.fingerprint)
    client = await connect(config)
    handle = await client.start_workflow(
        workflow_name,
        argument,
        id=task_id,
        task_queue=config.task_queue,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        result_type=PipelineRun,
    )
    return {"task_id": task_id, "workflow_id": handle.id, "run_id": handle.first_execution_run_id}


async def decision(service: PipelineService, task_id: str, fingerprint: str, config: TemporalConfig, *, reject=False):
    plan = service.ledger.plan(task_id)
    root_task_id = task_id
    if is_child_plan(plan):
        _require_editing_migration(service)
        service.checked_plan(task_id, fingerprint)
        from ...ui_analysis.editing import EditingExecution

        EditingExecution(service).validate_current(plan)
        if any(operation.state in {"succeeded", "failed"} for operation in service.ledger.list_operations(task_id)):
            root_hint = plan.parameters.get("root_task_id")
            raise PipelineError(
                "child_terminal",
                f"Editing child {task_id} is terminal; approve the current child under root {root_hint!s}",
            )
        root_task_id = str(plan.parameters["root_task_id"])
    elif plan.workflow_type == "ui_analysis":
        request = _ui_request(service, plan)
        if request.output_mode != "parse":
            _require_editing_migration(service)
        service.checked_plan(task_id, fingerprint)
    else:
        service.checked_plan(task_id, fingerprint)
    client = await connect(config)
    # The local receipt is mandatory; raw Temporal Updates cannot manufacture approval.
    request = approve(service.ledger, task_id, fingerprint, reject=reject)
    handle = client.get_workflow_handle(root_task_id)
    return await handle.execute_update("approval", request, id=request.request_id, result_type=str)


async def inspect(task_id: str, config: TemporalConfig):
    client = await connect(config)
    return await client.get_workflow_handle(task_id).query("state", result_type=PipelineRun)


async def cancel(service: PipelineService, task_id: str, config: TemporalConfig):
    service.ledger.plan(task_id)
    # Gate submission before the Update arrives, including a temporarily absent worker.
    service.ledger.request_cancel(task_id)
    client = await connect(config)
    return await client.get_workflow_handle(task_id).execute_update("cancel", result_type=str)


async def reconcile(task_id: str, config: TemporalConfig):
    client = await connect(config)
    return await client.get_workflow_handle(task_id).execute_update("reconcile", result_type=str)
