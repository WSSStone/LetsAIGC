from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer

from .comfy import ComfyClient
from .comfy.runtime import serve
from .doctor import run_doctor
from .errors import LetsAIGCError, ValidationError
from .evaluation import run_evaluation
from .exporter import export_run
from .models import ModelManager
from .paths import find_repo_root
from .runpack import build_runpack
from .schemas import LicenseLane
from .tracking import load_manifest, save_manifest
from .tracking.runtime import serve_mlflow
from .training import run_sdxl_lora
from .workflows import WorkflowRunner

app = typer.Typer(no_args_is_help=True, help="Reproducible local harness for game-asset AIGC")
models_app = typer.Typer(no_args_is_help=True)
comfy_app = typer.Typer(no_args_is_help=True)
workflow_app = typer.Typer(no_args_is_help=True)
train_app = typer.Typer(no_args_is_help=True)
eval_app = typer.Typer(no_args_is_help=True)
runpack_app = typer.Typer(no_args_is_help=True)
review_app = typer.Typer(no_args_is_help=True)
tracking_app = typer.Typer(no_args_is_help=True)
app.add_typer(models_app, name="models")
app.add_typer(comfy_app, name="comfy")
app.add_typer(workflow_app, name="workflow")
app.add_typer(train_app, name="train")
app.add_typer(eval_app, name="eval")
app.add_typer(runpack_app, name="runpack")
app.add_typer(review_app, name="review")
app.add_typer(tracking_app, name="tracking")


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
    _emit(ctx, {"host": "127.0.0.1", "port": 5000}, "starting MLflow at 127.0.0.1:5000")
    raise typer.Exit(serve_mlflow())


@app.command("export")
def export_command(
    ctx: typer.Context,
    run_id: str,
    destination: Path = typer.Option(..., "--to"),
    lane: LicenseLane = typer.Option(LicenseLane.production, "--lane"),
) -> None:
    _emit(ctx, export_run(run_id, destination, lane))


@runpack_app.command("build")
def runpack_build(ctx: typer.Context, run_id: str) -> None:
    path = build_runpack(run_id)
    _emit(ctx, {"run_id": run_id, "path": str(path)}, f"created {path}")


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
