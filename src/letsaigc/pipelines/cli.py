"""Human-controlled pipeline and runtime commands, never Agent capabilities."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import typer

from ..execution.temporal.config import diagnose, load_config, require_sdk, runtime_root
from ..schemas import GenerationPlan
from ..schemas.pipeline import canonical_json
from .errors import PipelineError
from .resources import owners
from .service import PipelineService

pipeline_app = typer.Typer(no_args_is_help=True, help="Plan and control durable pipelines")
runtime_app = typer.Typer(no_args_is_help=True, help="Manage the optional local Temporal runtime")


def emit(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def service() -> PipelineService:
    return PipelineService(runtime_root(), mlflow_enabled=load_config().mlflow_enabled)


def run(coroutine):
    try:
        return asyncio.run(coroutine)
    except PipelineError:
        raise
    except Exception:
        # Transport errors may contain credentials/URLs. Surface only a stable code.
        raise PipelineError(
            "temporal_request_failed", "Temporal request failed; inspect runtime and task state"
        ) from None


@pipeline_app.command("plan")
def plan(
    task_id: str | None = typer.Option(None, "--task-id"),
    generation_plan: Path | None = typer.Option(None, "--generation-plan", exists=True, dir_okay=False),
    revisions: int = typer.Option(0, min=0, max=10),
    accept_after: int = typer.Option(0, min=0, max=10),
):
    """Freeze a free simulation or an existing reviewed Comfy GenerationPlan."""
    instance = service()
    try:
        if generation_plan:
            if task_id or revisions or accept_after:
                raise PipelineError("invalid_plan", "Generation plan already defines task and budget")
            source = GenerationPlan.model_validate_json(generation_plan.read_text(encoding="utf-8"))
            result = instance.generation_plan(source)
        else:
            if not task_id:
                raise PipelineError("invalid_plan", "--task-id is required for a simulation")
            result = instance.smoke_plan(task_id, revisions=revisions, accept_after=accept_after)
    except PipelineError:
        raise
    except Exception:
        raise PipelineError("invalid_plan", "Plan validation failed") from None
    emit({"plan": result.model_dump(mode="json"), "fingerprint": result.fingerprint})


@pipeline_app.command("start")
def start(task_id: str):
    require_sdk()
    from ..execution.temporal.client import start as start_task

    emit(run(start_task(service(), task_id, load_config())))


@pipeline_app.command("approve")
def approve(task_id: str, fingerprint: str = typer.Option(..., "--fingerprint")):
    require_sdk()
    from ..execution.temporal.client import decision

    emit(run(decision(service(), task_id, fingerprint, load_config())))


@pipeline_app.command("reject")
def reject(task_id: str, fingerprint: str = typer.Option(..., "--fingerprint")):
    require_sdk()
    from ..execution.temporal.client import decision

    emit(run(decision(service(), task_id, fingerprint, load_config(), reject=True)))


@pipeline_app.command("inspect")
def inspect(task_id: str, local: bool = typer.Option(False, "--local")):
    instance = service()
    plan = instance.ledger.plan(task_id)
    if local:
        from ..execution.temporal.projection import local_projection

        state = local_projection(instance, task_id)
    else:
        require_sdk()
        from ..execution.temporal.client import inspect as inspect_task

        state = run(inspect_task(task_id, load_config())).model_dump(mode="json")
    emit(
        {
            "plan": json.loads(canonical_json(plan)),
            "state": state,
            "state_source": "local_projection" if local else "temporal_query",
            "operations": [item.model_dump(mode="json") for item in instance.ledger.list_operations(task_id)],
            "usage": {key: value.model_dump(mode="json") for key, value in instance.ledger.usage(task_id).items()},
            "resource_owners": owners(instance.ledger),
        }
    )


@pipeline_app.command("cancel")
def cancel(task_id: str):
    require_sdk()
    from ..execution.temporal.client import cancel as cancel_task

    emit(run(cancel_task(service(), task_id, load_config())))


@pipeline_app.command("reconcile")
def reconcile(task_id: str):
    """Retry provider lookup/collection; never assert unknown work is free."""
    require_sdk()
    from ..execution.temporal.client import reconcile as reconcile_task

    emit(run(reconcile_task(task_id, load_config())))


@pipeline_app.command("rebuild-projection")
def rebuild_projection(task_id: str):
    require_sdk()
    from ..execution.temporal.client import inspect as inspect_task
    from ..execution.temporal.projection import project

    state = run(inspect_task(task_id, load_config()))
    project(service(), state)
    emit({"task_id": task_id, "rebuilt": True})


@runtime_app.command("doctor")
def doctor():
    emit(diagnose(load_config()))


@runtime_app.command("worker")
def worker():
    require_sdk()
    from ..execution.temporal.worker import serve

    run(serve(load_config(), runtime_root()))


@runtime_app.command("dev-server")
def dev_server(
    binary: Path = typer.Option(..., "--binary", exists=True, dir_okay=False),
    database: Path | None = typer.Option(None, "--database"),
):
    """Explicit foreground launch; SQLite state persists across restarts."""
    config = load_config()
    host, port = config.address.rsplit(":", 1)
    path = (database or runtime_root() / "temporal-dev.sqlite").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(binary.resolve()),
            "server",
            "start-dev",
            "--ip",
            host,
            "--port",
            port,
            "--ui-ip",
            "127.0.0.1",
            "--db-filename",
            str(path),
        ],
        check=True,
    )
