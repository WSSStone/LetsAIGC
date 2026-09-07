# LetsAIGC Development Guidelines

Consolidated from all feature plans. Last updated: 2026-09-07

## Active Technologies

- Python 3.12 (core and ComfyUI), Python 3.10 (sd-scripts).
- Typer, Pydantic v2, PyYAML, JSON Schema, httpx, psutil, and OpenAI Python SDK (lazy runtime import).
- Native ComfyUI workflows and committed recipes; compiled graphs remain in `.local/agent`.
- System `ffmpeg` and `ffprobe`; Pillow for deterministic image/frame operations.
- `.local/agent/sessions` (including input copies), `.local/agent/tasks`, `.local/runs`, and `.local/models`.
- Local MLflow tracking and DVC pointers for promoted artifacts.

## Planned Technologies (013-game-ui-analysis)

- Isolated Python 3.12 OCR environment: PaddleOCR 3.4.0 and PaddlePaddle 3.2.2 CPU; static inference models require loader, license, hash, and runtime validation.
- Isolated SAM 2 environment: Transformers 4.57.6, platform-specific PyTorch 2.9.1/torchvision 0.24.1 builds, and official safetensors snapshots; Windows AMD64 is verified with the official cu130 wheels. No pickle checkpoint fallback or automatic model download.
- Manual single-image parse and SerpApi/Tavily search preview use Temporal SDK 1.32.0, the shared v4 pipeline ledger and ArtifactStore; T001–T018 are accepted. Full quality acceptance remains pending.
- Human correction preview (T043–T050): task-scoped loopback Python HTTP service, packaged HTML/CSS/ES modules and SVG editor, immutable review artifacts. No CDN, Node build or Torch dependency. Review v3→v4 and child-budget v4→v5 migrations are delivered; the existing local ledger stays v4 until an explicit offline migration.
- T020–T026 are accepted: offline reviewed/automatic selection CLI, guarded SAM adapter and mask/alpha/glyph artifacts, native masked compilation and CPU composition. Core has no Torch dependency. Windows T024 verified the exact official SAM 2.1 snapshot on the cu130 environment with one approved fixed development sample, settled actual GPU time, and confirmed zero allocated CUDA bytes after release. ComfyUI validation (T027), editing workflow execution (T028), and full quality acceptance remain pending.
- Human review save/confirm has zero model/GPU calls and no approval authority. Preserve raw OCR/model outputs; freeze a confirmed review version for downstream processing.

## Project Structure

```text
src/
tests/
```

## Commands

Run from the repository root:

```powershell
conda run --no-capture-output -n letsaigc-core pytest
conda run --no-capture-output -n letsaigc-core ruff check .
```

## Code Style

Python 3.12 (core and ComfyUI), Python 3.10 (sd-scripts): Follow standard conventions

## Recent Changes

- Game UI preview T001–T018 accepted; planned US7 human correction adds T043–T050 before segmentation/inpainting. See `specs/013-game-ui-analysis/tasks.md`; preserve old task IDs and historical evidence.
- Consolidated delivered work onto `master`; specification IDs remain stable and do not name active branches.
- Added multimodal Agent critique and media orchestration with MLflow lineage.
- Added constrained ComfyUI recipe compilation and validated local/remote generation assets.

<!-- MANUAL ADDITIONS START -->
- Treat `.doc/`, `.local/`, base-model weights, secrets and transient outputs as local-only.
- Use `conda run --no-capture-output -n letsaigc-core ...`; do not install project packages into global Python.
- Keep ComfyUI and MLflow bound to `127.0.0.1`; v1 permits no third-party custom nodes.
- Never accept a gated model license, change PowerShell execution policy, or silently reduce an effective training profile.
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
- Verify with `conda run --no-capture-output -n letsaigc-core pytest` and `conda run --no-capture-output -n letsaigc-core ruff check .`.
<!-- MANUAL ADDITIONS END -->
