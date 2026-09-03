# LetsAIGC Development Guidelines

Consolidated from all feature plans. Last updated: 2026-09-03

## Active Technologies

- Python 3.12 (core and ComfyUI), Python 3.10 (sd-scripts).
- Typer, Pydantic v2, PyYAML, JSON Schema, httpx, psutil, and OpenAI Python SDK (lazy runtime import).
- Native ComfyUI workflows and committed recipes; compiled graphs remain in `.local/agent`.
- System `ffmpeg` and `ffprobe`; Pillow for deterministic image/frame operations.
- `.local/agent/sessions` (including input copies), `.local/agent/tasks`, `.local/runs`, and `.local/models`.
- Local MLflow tracking and DVC pointers for promoted artifacts.

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

- Consolidated delivered work onto `master`; specification IDs remain stable and do not name active branches.
- Added multimodal Agent critique and media orchestration with MLflow lineage.
- Added constrained ComfyUI recipe compilation and validated local/remote generation assets.

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
- Video/runpack work must record declared media roles, FFmpeg versions and source hashes; never
  package credentials, absolute paths or model weights.
- Treat `.agents/`, `.specify/`, `AGENTS.md`, and `specs/` as the Development Harness only;
  never expose them as LetsAIGC runtime features or call the product a harness.
- Agent runtime tools must be allowlisted and sequential. They must not provide shell, arbitrary
  writes, model download, training, human approval, or production export capabilities.
- Paid media and GPU generation require an exact approval fingerprint and per-iteration/total
  budget; credentials and URL query/fragment values must never enter logs, manifests, or runpacks.
- Verify with `mamba run -n letsaigc-core pytest` and `mamba run -n letsaigc-core ruff check .`.
<!-- MANUAL ADDITIONS END -->
