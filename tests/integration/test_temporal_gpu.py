"""Real GPU acceptance. Requires a locally frozen plan AND its explicit approved fingerprint."""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

PLAN_PATH = os.getenv("LETSAIGC_TEMPORAL_GPU_PLAN")
APPROVAL = os.getenv("LETSAIGC_TEMPORAL_GPU_APPROVAL")
CLI = os.getenv("LETSAIGC_TEMPORAL_TEST_CLI")
pytestmark = [
    pytest.mark.runtime,
    pytest.mark.gpu,
    pytest.mark.skipif(
        not (PLAN_PATH and APPROVAL and CLI),
        reason="Real GPU test requires frozen plan path, explicit approval fingerprint and verified CLI",
    ),
]


def test_temporal_real_comfy_generation():
    pytest.importorskip("temporalio")
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from letsaigc.execution.temporal.activities import PipelineActivities
    from letsaigc.execution.temporal.messages import WorkflowInput
    from letsaigc.execution.temporal.smoke_workflow import ComfyGenerationWorkflow
    from letsaigc.pipelines.approval import approve
    from letsaigc.pipelines.service import PipelineService
    from letsaigc.schemas.pipeline import PipelinePlan, PipelineRun, PipelineState

    path = Path(PLAN_PATH)
    plan = PipelinePlan.model_validate_json(path.read_text(encoding="utf-8"))
    assert plan.workflow_type == "comfy_generation"
    assert plan.fingerprint == APPROVAL
    service = PipelineService(path.parent)
    assert service.ledger.plan(plan.task_id) == plan
    assert not service.ledger.list_operations(plan.task_id), "Reconcile the existing attempt before another GPU test"

    async def scenario():
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=CLI,
            dev_server_database_filename=str(path.parent / "temporal.sqlite"),
            data_converter=pydantic_data_converter,
            ui=False,
            dev_server_log_level="error",
        ) as env:
            with ThreadPoolExecutor(4) as executor:
                async with Worker(
                    env.client,
                    task_queue="gpu-validation",
                    workflows=[ComfyGenerationWorkflow],
                    activities=PipelineActivities(service).registered(),
                    activity_executor=executor,
                ):
                    handle = await env.client.start_workflow(
                        ComfyGenerationWorkflow.run,
                        WorkflowInput(plan=plan, poll_seconds=1),
                        id=plan.task_id,
                        task_queue="gpu-validation",
                        result_type=PipelineRun,
                    )

                    async def awaiting():
                        while True:
                            state = await handle.query("state", result_type=PipelineRun | None)
                            if state and state.state == PipelineState.awaiting_approval:
                                return
                            await asyncio.sleep(0.1)

                    await asyncio.wait_for(awaiting(), 30)
                    receipt = approve(service.ledger, plan.task_id, APPROVAL)
                    await handle.execute_update("approval", receipt, id=receipt.request_id)
                    try:
                        result = await asyncio.wait_for(handle.result(), 240)
                    finally:
                        history = await handle.fetch_history()
                        (path.parent / "gpu-history.json").write_text(history.to_json(), encoding="utf-8")
                    (path.parent / "gpu-result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
                    assert result.state == PipelineState.succeeded, result.stop_reason
                    assert len(result.artifacts) == 1
                    assert len(service.ledger.list_operations(plan.task_id)) == 1
                    output = service.artifacts.resolve(result.artifacts[0])
                    from PIL import Image

                    with Image.open(output) as image:
                        assert image.size == (512, 512)
                    usage = {name: cost.model_dump() for name, cost in service.ledger.usage(plan.task_id).items()}
                    (path.parent / "gpu-usage.json").write_text(json.dumps(usage, indent=2), encoding="utf-8")
                    assert usage["unsettled"] == {"cost_usd": 0, "gpu_minutes": 0}
                    assert usage["actual"]["gpu_minutes"] <= plan.envelope.budget.max_total_gpu_minutes

    asyncio.run(scenario())
