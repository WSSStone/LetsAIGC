# Implementation Plan: Video Runtime and Models

> **Historical scope note (2026-09-03):** This plan records the expert video pipeline
> and the additive RunManifest 1.1 design. Its "harness" terminology predates the
> Agent-first product boundary; the current workbench uses compatible RunManifest
> 1.2 evidence. See the [current architecture](../../docs/agent-first-architecture.md)
> and [Agent guide](../../docs/agent-quickstart.md). Original design and acceptance
> evidence remain historical, not proof of Agent-path live acceptance.

**Feature ID**: `005-video-runtime-models` | **Date**: 2026-08-31 | **Spec**: [spec.md](spec.md)

## Summary

Upgrade the existing image-centric workflow runner and manifest to support declared media outputs and lineage, add a guarded system FFmpeg/ffprobe adapter, provide local Wan2.1 video generation plus external import and provider-neutral runpacks, and retain backward compatibility with all image and training flows.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Existing Pydantic, Typer, httpx, MLflow stack; system `ffmpeg` and `ffprobe`; Pillow for deterministic image/frame operations in feature 006  
**Storage**: `.local/runs`, `.local/models`, local MLflow, DVC pointers for promoted artifacts  
**Testing**: pytest unit, contract, CPU-only FFmpeg integration, optional runtime/GPU smoke tests  
**Target Platform**: Windows 11, RTX 3080 10 GiB, 64 GiB RAM  
**Project Type**: Local Python CLI and ComfyUI orchestration harness  
**Performance Goals**: Fixed 512×512/17-frame Wan2.1 smoke must complete without OOM; CPU fixture probe/import and runpack tests under 30 seconds  
**Constraints**: Loopback-only ComfyUI; no custom nodes; user-managed PATH FFmpeg; no shell-string execution; new contracts collect declared outputs only  
**Scale/Scope**: One operator, one local GPU, short clips, at most tens of artifacts per run  
**License Lanes**: Wan2.1 production candidate under Apache-2.0; Wan2.2 5B experimental until local approval; large models registry/runpack only  
**Resource Budget**: 10 GiB VRAM, 64 GiB RAM, model-profile disk budgets, workflow dimensions/FPS/frame count/duration/timeout, temporary disk tracked per contract  
**Provenance**: Git/Comfy/model/workflow hashes, effective inputs and seed, FFmpeg build, environment, media probes, output hashes, MLflow run ID, parent/source lineage

## Constitution Check

- **Reproducibility — PASS**: media commands, revisions, hashes, seed, probe results, environment and outputs are captured.
- **License segregation — PASS**: model/import metadata carries a lane; unknown and incomplete licenses cannot reach production.
- **Local-first budget — PASS**: Wan2.1 1.3B is the required local smoke; larger models use provider-neutral runpacks.
- **Workflow as code — PASS**: UI/API workflows and typed output contracts remain committed and tested.
- **Provenance and quality — PASS**: manifests and MLflow preserve evidence; export still requires automation and human approval.

Post-design re-check: PASS. No constitution exception is required.

## Design

1. Extend schemas additively: `RunManifest` 1.1 accepts old 1.0 records, `RunOutput` gains optional media/lineage fields, and `WorkflowContract` gains optional declared outputs plus temporal budgets. Repository contracts are migrated; external legacy image contracts retain image scanning.
2. Add a `media` boundary that resolves and probes tools, executes argument arrays with timeouts, validates paths/extensions, normalizes fractions, and returns typed metadata. It never invokes a shell.
3. Generalize `WorkflowRunner` output discovery. New contracts map a node and history field to a role/media kind; declared results are resolved through the existing Comfy output boundary, probed and hashed. Legacy contracts keep the old image-only path.
4. Add `video run` as a video-contract facade over `WorkflowRunner`, and `video import` to copy a source into the run directory and create a governed video manifest.
5. Extend runpacks with job mode and result ingestion. A canonical JSON fingerprint binds the request to its result metadata; secrets, weights, absolute local paths and provider executables are rejected.
6. Log manifest and selected preview artifacts to MLflow. Raw frames remain local; downstream feature manifests use `parent_run_id` and source artifact hashes.

## Project Structure

```text
src/letsaigc/
├── media/                 # tools, probes, artifact discovery and imports
├── schemas/models.py      # additive media/job/lineage contracts
├── workflows/runner.py    # declared output collection
├── runpack.py             # job build and result ingest
├── doctor.py              # FFmpeg capability checks
└── cli.py                 # video and extended runpack commands

configs/
├── models/catalog.yaml
└── video/

workflows/{ui,api,contracts}/
tests/{unit,contract,integration}/
```

**Structure Decision**: Extend the existing single-package CLI. Media is a reusable boundary shared by sprites and drama; no new service or environment is introduced.

## Delivery Order

1. Schema and contract migration with failing tests.
2. FFmpeg probe/doctor and media import.
3. Declared Comfy media outputs and video CLI.
4. Model catalog/workflow and runpack job/ingest.
5. CPU regression suite, then optional local model/runtime smoke.
