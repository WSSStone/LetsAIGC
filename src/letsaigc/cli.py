from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer

from .agent import AgentOrchestrator
from .agent.orchestrator import load_budget
from .comfy import ComfyClient
from .comfy.runtime import serve
from .config import get_int_setting, get_setting, load_workflow_contract
from .doctor import run_doctor
from .drama import render_drama
from .errors import LetsAIGCError, ValidationError
from .evaluation import run_evaluation
from .exporter import export_run
from .media import import_video
from .models import ModelManager
from .paths import find_repo_root
from .pipelines.cli import pipeline_app, runtime_app
from .runpack import build_job_runpack, build_runpack, ingest_job_result
from .schemas import BackendName, LicenseLane
from .sprites import build_sprite_sequence, validate_sprite_run
from .tracking import load_manifest, save_manifest
from .tracking.runtime import serve_mlflow
from .training import run_sdxl_lora
from .ui_analysis.cli import ui_app
from .workflows import WorkflowRunner

app = typer.Typer(no_args_is_help=True, help="Agent-driven game asset generation workbench")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(runtime_app, name="runtime")
app.add_typer(ui_app, name="ui")
agent_app = typer.Typer(no_args_is_help=True, help="Plan, approve, execute, and inspect Agent tasks")
models_app = typer.Typer(no_args_is_help=True)
comfy_app = typer.Typer(no_args_is_help=True)
workflow_app = typer.Typer(no_args_is_help=True)
video_app = typer.Typer(no_args_is_help=True)
sprites_app = typer.Typer(no_args_is_help=True)
drama_app = typer.Typer(no_args_is_help=True)
train_app = typer.Typer(no_args_is_help=True)
eval_app = typer.Typer(no_args_is_help=True)
runpack_app = typer.Typer(no_args_is_help=True)
review_app = typer.Typer(no_args_is_help=True)
tracking_app = typer.Typer(no_args_is_help=True)
app.add_typer(agent_app, name="agent")
app.add_typer(models_app, name="models")
app.add_typer(comfy_app, name="comfy")
app.add_typer(workflow_app, name="workflow")
app.add_typer(video_app, name="video")
app.add_typer(sprites_app, name="sprites")
app.add_typer(drama_app, name="drama")
app.add_typer(train_app, name="train")
app.add_typer(eval_app, name="eval")
app.add_typer(runpack_app, name="runpack")
app.add_typer(review_app, name="review")
app.add_typer(tracking_app, name="tracking")


@agent_app.command("plan")
def agent_plan(
    ctx: typer.Context,
    intent: str,
    images: list[str] = typer.Option(None, "--image", help="Local image path or HTTPS URL; repeatable"),
    backend: BackendName = typer.Option(BackendName.auto, "--backend"),
    budget: Path = typer.Option(..., "--budget"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Create a budgeted plan and approval fingerprint without generating media."""
    result = AgentOrchestrator().plan(
        intent,
        image_sources=images or [],
        requested_backend=backend,
        budget=load_budget(budget),
        session_id=session,
    )
    _emit(ctx, result)


@agent_app.command("execute")
def agent_execute(
    ctx: typer.Context,
    task_id: str,
    fingerprint: str = typer.Option(..., "--approve"),
) -> None:
    """Execute only the exact immutable plan identified by FINGERPRINT."""
    _emit(ctx, AgentOrchestrator().execute(task_id, approval_fingerprint=fingerprint))


@agent_app.command("run")
def agent_run(
    ctx: typer.Context,
    intent: str,
    images: list[str] = typer.Option(None, "--image", help="Local image path or HTTPS URL; repeatable"),
    backend: BackendName = typer.Option(BackendName.auto, "--backend"),
    budget: Path = typer.Option(..., "--budget"),
) -> None:
    """Plan, display, and in human mode ask before executing."""
    orchestrator = AgentOrchestrator()
    planned = orchestrator.plan(
        intent,
        image_sources=images or [],
        requested_backend=backend,
        budget=load_budget(budget),
    )
    if ctx.obj.get("json"):
        _emit(ctx, planned)
        return
    _emit(ctx, planned)
    if not typer.confirm("Execute this exact plan and budget?"):
        _emit(ctx, orchestrator.reject(planned["id"]), "task rejected")
        return
    _emit(
        ctx,
        orchestrator.execute(planned["id"], approval_fingerprint=planned["plan_fingerprint"]),
    )


@agent_app.command("inspect")
def agent_inspect(ctx: typer.Context, identifier: str) -> None:
    """Inspect a local Agent session or task without provider access."""
    _emit(ctx, AgentOrchestrator().inspect(identifier))


@agent_app.command("chat")
def agent_chat(
    ctx: typer.Context,
    session: str | None = typer.Option(None, "--session"),
    budget: Path = typer.Option(Path("configs/agent/budget-local.yaml"), "--budget"),
) -> None:
    """Run a local multi-turn shell; every media task still requires approval."""
    if ctx.obj.get("json"):
        raise ValidationError("agent chat is interactive and unavailable with --json")
    orchestrator = AgentOrchestrator()
    current_session = session
    typer.echo("LetsAIGC Agent chat. Enter /exit to stop.")
    while True:
        text = typer.prompt("intent")
        if text.strip().lower() in {"/exit", "/quit"}:
            return
        planned = orchestrator.plan(
            text,
            image_sources=[],
            requested_backend=BackendName.auto,
            budget=load_budget(budget),
            session_id=current_session,
        )
        current_session = planned["session_id"]
        _emit(ctx, planned)
        if typer.confirm("Execute this exact plan and budget?"):
            _emit(
                ctx,
                orchestrator.execute(planned["id"], approval_fingerprint=planned["plan_fingerprint"]),
            )
        else:
            orchestrator.reject(planned["id"])


@app.callback()
def root(
    ctx: typer.Context, json_output: bool = typer.Option(False, "--json", help="Emit JSON")
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output


def _emit(ctx: typer.Context, payload: Any, message: str | None = None) -> None:
    if ctx.obj.get("json"):
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    elif message:
        typer.echo(message)
    elif isinstance(payload, list):
        for item in payload:
            typer.echo(json.dumps(item, ensure_ascii=False, default=str))
    else:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Inspect hardware, storage, tools, environments, services and models."""
    result = run_doctor()
    _emit(ctx, result, f"doctor: {result['status']}")
    if result["status"] == "fail":
        raise typer.Exit(3)


@app.command()
def bootstrap(
    ctx: typer.Context,
    component: str = typer.Option(..., "--component", help="core, comfy, or train"),
) -> None:
    """Create/update one isolated component environment."""
    if component not in {"core", "comfy", "train"}:
        raise ValidationError(f"Unknown component: {component}")
    script = find_repo_root() / "scripts/bootstrap.ps1"
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(script), "-Component", component],
        cwd=find_repo_root(),
        check=False,
    )
    if completed.returncode:
        raise LetsAIGCError(f"Bootstrap failed with exit {completed.returncode}")
    _emit(ctx, {"component": component, "status": "completed"}, f"bootstrapped {component}")


@models_app.command("list")
def models_list(ctx: typer.Context) -> None:
    result = ModelManager().list()
    _emit(ctx, result)


@models_app.command("verify")
def models_verify(ctx: typer.Context, profile: str) -> None:
    result = ModelManager().verify_profile(profile)
    _emit(ctx, result)


@models_app.command("sync")
def models_sync(ctx: typer.Context, profile: str) -> None:
    result = ModelManager().sync(profile)
    _emit(ctx, result)


@comfy_app.command("status")
def comfy_status(ctx: typer.Context) -> None:
    client = ComfyClient()
    result = client.status()
    result["websocket"] = client.websocket_probe()
    _emit(ctx, result)


@comfy_app.command("serve")
def comfy_serve(ctx: typer.Context) -> None:
    _emit(ctx, {"host": "127.0.0.1", "port": 8188}, "starting ComfyUI at 127.0.0.1:8188")
    raise typer.Exit(serve())


def _parse_overrides(items: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValidationError(f"Expected KEY=VALUE: {item}")
        key, raw = item.split("=", 1)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        result[key] = value
    return result


@workflow_app.command("run")
def workflow_run(
    ctx: typer.Context,
    workflow_id: str,
    set_values: list[str] = typer.Option(None, "--set", help="Override KEY=VALUE"),
) -> None:
    _emit(ctx, WorkflowRunner().run(workflow_id, _parse_overrides(set_values or [])))


@video_app.command("run")
def video_run(
    ctx: typer.Context,
    workflow_id: str,
    set_values: list[str] = typer.Option(None, "--set", help="Override KEY=VALUE"),
) -> None:
    contract = load_workflow_contract(workflow_id)
    if contract.media_kind != "video":
        raise ValidationError(f"Workflow is not declared as video media: {workflow_id}")
    _emit(ctx, WorkflowRunner().run(workflow_id, _parse_overrides(set_values or [])))


@video_app.command("import")
def video_import(
    ctx: typer.Context,
    path: Path,
    metadata: Path = typer.Option(..., "--metadata"),
) -> None:
    _emit(ctx, import_video(path, metadata))


@sprites_app.command("build")
def sprites_build(
    ctx: typer.Context,
    config: Path = typer.Option(..., "--config"),
    source_run: str | None = typer.Option(None, "--source-run"),
    input_path: Path | None = typer.Option(None, "--input"),
) -> None:
    _emit(
        ctx,
        build_sprite_sequence(
            source_run_id=source_run,
            input_path=input_path,
            profile_path=config,
        ),
    )


@sprites_app.command("validate")
def sprites_validate(ctx: typer.Context, run_id: str) -> None:
    _emit(ctx, validate_sprite_run(run_id))


@drama_app.command("render")
def drama_render(
    ctx: typer.Context,
    project: Path = typer.Option(..., "--project"),
    resume: bool = typer.Option(False, "--resume"),
) -> None:
    _emit(ctx, render_drama(project, resume=resume))


@train_app.command("sdxl-lora")
def train_sdxl_lora(ctx: typer.Context, config: Path = typer.Option(..., "--config")) -> None:
    _emit(ctx, run_sdxl_lora(config))


@eval_app.command("run")
def eval_run(ctx: typer.Context, suite: str) -> None:
    _emit(ctx, run_evaluation(suite))


@review_app.command("approve")
def review_approve(
    ctx: typer.Context,
    run_id: str,
    reviewer: str = typer.Option(..., "--by"),
    note: str = typer.Option(..., "--note"),
) -> None:
    manifest = load_manifest(run_id)
    manifest.governance.human_approved = True
    manifest.governance.human_review = {"reviewer": reviewer, "note": note}
    save_manifest(manifest)
    _emit(ctx, {"run_id": run_id, "human_approved": True}, f"approved {run_id}")


@tracking_app.command("serve")
def tracking_serve(ctx: typer.Context) -> None:
    host = get_setting("MLFLOW_HOST", "127.0.0.1")
    port = get_int_setting("MLFLOW_PORT", 5000)
    _emit(ctx, {"host": host, "port": port}, f"starting MLflow at {host}:{port}")
    raise typer.Exit(serve_mlflow(host=host, port=port))


@app.command("export")
def export_command(
    ctx: typer.Context,
    run_id: str,
    destination: Path = typer.Option(..., "--to"),
    lane: LicenseLane = typer.Option(LicenseLane.production, "--lane"),
) -> None:
    _emit(ctx, export_run(run_id, destination, lane))


@runpack_app.command("build")
def runpack_build(
    ctx: typer.Context,
    run_id: str | None = typer.Argument(None),
    job: Path | None = typer.Option(None, "--job"),
) -> None:
    if bool(run_id) == bool(job):
        raise ValidationError("Specify exactly one of RUN_ID or --job")
    if job is not None:
        result = build_job_runpack(job)
        payload = {"path": str(result.path), "fingerprint": result.fingerprint}
        _emit(ctx, payload, f"created {result.path}")
        return
    path = build_runpack(str(run_id))
    _emit(ctx, {"run_id": run_id, "path": str(path)}, f"created {path}")


@runpack_app.command("ingest")
def runpack_ingest(
    ctx: typer.Context,
    runpack: Path,
    result: Path = typer.Option(..., "--result"),
    metadata: Path = typer.Option(..., "--metadata"),
) -> None:
    _emit(ctx, ingest_job_result(runpack, result, metadata))


def main() -> None:
    try:
        app()
    except LetsAIGCError as exc:
        payload = {
            "status": "error",
            "error": type(exc).__name__,
            "message": str(exc),
            "details": exc.details,
        }
        if "--json" in sys.argv:
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2), err=True)
        else:
            typer.echo(f"error: {exc}", err=True)
        raise SystemExit(exc.exit_code) from exc
