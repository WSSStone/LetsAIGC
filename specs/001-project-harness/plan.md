# Implementation Plan: LetsAIGC Project Harness

**Branch**: `001-project-harness` | **Date**: 2026-08-31 | **Spec**: [spec.md](./spec.md)  
**Input**: Initialize a local-first, reproducible 2D game-asset AIGC workspace.

## Summary

Build a Python CLI and versioned configuration harness around three isolated Conda
environments: core governance/automation, pinned ComfyUI inference, and low-VRAM
SDXL LoRA training. Model synchronization is catalog-driven and hash-verified;
workflows are validated before ComfyUI submission; every run emits a provenance
manifest and optional MLflow record. DVC owns datasets and promoted adapters while
third-party weights, runtime state, outputs, secrets, and local design notes remain
outside Git.

## Technical Context

**Language/Version**: Python 3.12 (core and ComfyUI), Python 3.10 (sd-scripts)  
**Primary Dependencies**: Typer, Pydantic v2, PyYAML, JSON Schema, httpx, psutil,
MLflow, DVC; pinned ComfyUI and sd-scripts source trees  
**Storage**: YAML/JSON manifests, SQLite-backed local MLflow, filesystem DVC remote  
**Testing**: pytest, jsonschema contract tests, PowerShell smoke orchestration  
**Target Platform**: Windows 11 Pro, NVIDIA RTX 3080 10 GiB, loopback-only services  
**Project Type**: single Python CLI plus managed external runtimes  
**Performance Goals**: diagnostic response under 15 seconds; pre-submit validation
under 1 second; fixed-seed smoke runs complete without false success  
**Constraints**: no system CUDA Toolkit requirement, no global Python mutation, no
custom nodes in v1, no automatic license acceptance, no service beyond 127.0.0.1  
**Scale/Scope**: single user, one GPU, 2D image assets, five cataloged model families,
one SDXL LoRA training path  
**License Lanes**: SD1.5/SDXL/Schnell are production candidates; SD3.5 Medium is
conditional; FLUX.1 Dev is restricted and catalog-only; unknown is denied  
**Resource Budget**: 10 GiB VRAM, 64 GiB RAM, at least 150 GiB free disk before the
full baseline profile; SDXL LoRA uses batch 1, rank 8, gradient checkpointing,
caches and 512–768 px buckets  
**Provenance**: Git/runtime/model/workflow/config revisions and hashes, seed,
hardware, outputs, DVC revision, adapter lineage and MLflow run ID

## Constitution Check

*GATE: Passed before Phase 0 and re-checked after Phase 1.*

- **Reproducibility — PASS**: manifests and locks capture mutable dependencies.
- **License segregation — PASS**: catalog lanes and local attestations gate sync,
  use and export independently.
- **Local-first budget — PASS**: v1 targets fit the 3080 baseline except explicitly
  excluded FLUX LoRA, full fine-tuning and optional cloud jobs.
- **Workflow as code — PASS**: UI/API JSON, contracts and budgets are committed.
- **Provenance and quality — PASS**: DVC/MLflow and automated/human gates are defined.

## Project Structure

```text
specs/001-project-harness/{spec,plan,research,data-model,quickstart,tasks}.md
specs/001-project-harness/contracts/
configs/{models,policies,runtime,training,eval}/
workflows/{ui,api,contracts}/
datasets/  artifacts/  registry/adapters/
src/letsaigc/{comfy,models,workflows,training,tracking,policy,schemas}/
environment/  scripts/  tests/{unit,contract,integration,fixtures}/
```

**Structure Decision**: One installable `src`-layout Python application owns
governance and orchestration. Pinned ComfyUI/sd-scripts source, models, datasets and
outputs remain under `.local`; Git stores only code, configuration and pointers.

## Implementation Phases

1. Establish Git boundaries, constitution, Speckit artifacts and local reports.
2. Implement typed configuration, policy gates, diagnostics and the CLI surface.
3. Implement model sync, ComfyUI orchestration, manifests, DVC/MLflow, evaluation,
   export and provider-neutral runpacks.
4. Add contract/failure tests and bootstrap three isolated environments.
5. Deploy pinned runtimes, sync allowed models and execute inference/LoRA smoke tests.

## Complexity Tracking

No constitution violations are required. External runtimes are isolated rather
than vendored, and cloud execution is limited to a provider-neutral runpack.
