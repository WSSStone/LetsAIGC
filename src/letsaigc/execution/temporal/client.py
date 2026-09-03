"""Expert local entrypoint. Importing this module does not require the SDK."""

from __future__ import annotations

from ...pipelines.approval import approve
from ...pipelines.registry import WORKFLOWS
from ...pipelines.service import PipelineService
from ...schemas.pipeline import PipelineRun
from .config import TemporalConfig, require_sdk
from .messages import WorkflowInput


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

    plan = service.ledger.plan(task_id)
    service.checked_plan(task_id, plan.fingerprint)
    client = await connect(config)
    handle = await client.start_workflow(
        WORKFLOWS[plan.workflow_type][0],
        WorkflowInput(
            plan=plan,
            poll_seconds=config.poll_seconds,
            observations_per_run=config.observations_per_run,
            activity_timeout_seconds=config.activity_timeout_seconds,
        ),
        id=task_id,
        task_queue=config.task_queue,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        result_type=PipelineRun,
    )
    return {"task_id": task_id, "workflow_id": handle.id, "run_id": handle.first_execution_run_id}


async def decision(service: PipelineService, task_id: str, fingerprint: str, config: TemporalConfig, *, reject=False):
    client = await connect(config)
    # The local receipt is mandatory; raw Temporal Updates cannot manufacture approval.
    request = approve(service.ledger, task_id, fingerprint, reject=reject)
    handle = client.get_workflow_handle(task_id)
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
