"""Fixed registry and bounded worker concurrency; no runtime tool discovery."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ...pipelines.service import PipelineService
from .config import TemporalConfig, require_sdk


async def serve(config: TemporalConfig, root: Path):
    require_sdk()
    from temporalio.worker import Worker

    from .activities import PipelineActivities
    from .client import connect
    from .smoke_workflow import ComfyGenerationWorkflow, SmokeWorkflow

    client = await connect(config)
    service = PipelineService(root, mlflow_enabled=config.mlflow_enabled)
    activities = PipelineActivities(service)
    with ThreadPoolExecutor(max_workers=config.max_concurrent_activities) as executor:
        async with Worker(
            client,
            task_queue=config.task_queue,
            workflows=[SmokeWorkflow, ComfyGenerationWorkflow],
            activities=activities.registered(),
            activity_executor=executor,
            max_concurrent_activities=config.max_concurrent_activities,
            # v1 implementations and queue stay replay-compatible. Breaking code gets
            # new workflow/activity names plus a new queue, retaining old workers.
            build_id="letsaigc-pipelines-v1",
        ):
            import asyncio

            await asyncio.Event().wait()
