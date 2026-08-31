# LetsAIGC Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-08-31

## Active Technologies

- Python 3.12 (core and ComfyUI), Python 3.10 (sd-scripts) + Typer, Pydantic v2, PyYAML, JSON Schema, httpx, psutil, (001-project-harness)

## Project Structure

```text
src/
tests/
```

## Commands

cd src; pytest; ruff check .

## Code Style

Python 3.12 (core and ComfyUI), Python 3.10 (sd-scripts): Follow standard conventions

## Recent Changes

- 001-project-harness: Added Python 3.12 (core and ComfyUI), Python 3.10 (sd-scripts) + Typer, Pydantic v2, PyYAML, JSON Schema, httpx, psutil,

<!-- MANUAL ADDITIONS START -->
- Treat `.doc/`, `.local/`, base-model weights, secrets and transient outputs as local-only.
- Use `mamba run -n letsaigc-core ...`; do not install project packages into global Python.
- Keep ComfyUI and MLflow bound to `127.0.0.1`; v1 permits no third-party custom nodes.
- Never accept a gated model license, kill GPU processes, change PowerShell execution policy,
  create a Git commit, or silently reduce an effective training profile.
- Update UI JSON, API JSON and the workflow contract together. Every inference/training run
  must preserve revisions, hashes, seeds, effective parameters and output evidence.
- Production export requires a production license lane, verified hashes, succeeded checks,
  complete provenance and explicit human approval.
- Verify with `mamba run -n letsaigc-core pytest` and `mamba run -n letsaigc-core ruff check .`.
<!-- MANUAL ADDITIONS END -->
