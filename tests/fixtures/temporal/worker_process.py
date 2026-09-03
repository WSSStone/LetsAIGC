"""Subprocess fault fixture; never imported by product workers."""

import asyncio
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from temporalio.worker import Worker

from letsaigc.execution.temporal.activities import PipelineActivities
from letsaigc.execution.temporal.client import connect
from letsaigc.execution.temporal.config import TemporalConfig
from letsaigc.execution.temporal.smoke_workflow import SmokeWorkflow
from letsaigc.pipelines.contracts import Capability, Observation
from letsaigc.pipelines.service import PipelineService
from letsaigc.pipelines.simulation import SimulationBackend


class AuditedSimulation(SimulationBackend):
    capability = Capability(id="simulation.generate", resource="local-gpu", idempotent_submission=False)

    def submit(self, key, arguments):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS submission_calls(operation_id TEXT)")
            db.execute("INSERT INTO submission_calls VALUES(?)", (key,))
        return super().submit(key, arguments)


class RunningSimulation(AuditedSimulation):
    def inspect(self, receipt):
        return Observation(state="running")


class CrashAfterAccept(AuditedSimulation):
    def submit(self, key, arguments):
        super().submit(key, arguments)
        os._exit(23)


async def main():
    address, data_root, mode = sys.argv[1:]
    root = Path(data_root)
    backend_type = {"crash": CrashAfterAccept, "recover": AuditedSimulation, "running": RunningSimulation}[mode]
    backend = backend_type(root / "provider.sqlite")
    service = PipelineService(root, backends={"simulation.generate": backend})
    client = await connect(TemporalConfig(address=address, task_queue="test-pipelines"))
    with ThreadPoolExecutor(4) as executor:
        async with Worker(
            client,
            task_queue="test-pipelines",
            workflows=[SmokeWorkflow],
            activities=PipelineActivities(service).registered(),
            activity_executor=executor,
            build_id="fixture-crash-v1" if mode == "crash" else "fixture-recovery-v1",
        ):
            await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
