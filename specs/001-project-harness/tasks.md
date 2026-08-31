# Tasks: LetsAIGC Project Harness

## Phase 1: Governance and planning

- [X] T001 Create local-only and binary safety rules in `.gitignore`
- [X] T002 Define five principles in `.specify/memory/constitution.md`
- [X] T003 Align Speckit templates in `.specify/templates/`
- [X] T004 Write requirements and checklist in `specs/001-project-harness/`
- [X] T005 Write plan, research, data model, contracts and quickstart

## Phase 2: Repository foundation

- [X] T006 Create the source/config/workflow/data/test directory tree
- [X] T007 Write project overview and lifecycle in `README.md`
- [X] T008 Write repository agent guidance in `AGENTS.md`
- [X] T009 Create package and CLI entry point in `pyproject.toml`
- [X] T010 Add safe local configuration examples in `.env.example`
- [X] T011 Write `.doc/project-harness-roadmap.md`
- [X] T012 Write `.doc/local-aigc-feasibility-and-evaluation.md`

## Phase 3: Typed configuration and policy

- [X] T013 [P] Define model catalog in `configs/models/catalog.yaml`
- [X] T014 [P] Define license/export policy in `configs/policies/license-policy.yaml`
- [X] T015 [P] Define ComfyUI lock in `configs/runtime/comfyui.lock.yaml`
- [X] T016 [P] Define SDXL LoRA profiles in `configs/training/`
- [X] T017 [P] Define 2D evaluation suite in `configs/eval/2d-baseline.yaml`
- [X] T018 Implement public types in `src/letsaigc/schemas/models.py`
- [X] T019 Implement configuration loading in `src/letsaigc/config.py`
- [X] T020 Implement license/hash/promotion policy in `src/letsaigc/policy/`

## Phase 4: Diagnostics, models and runtime

- [X] T021 Implement readiness diagnostics in `src/letsaigc/doctor.py`
- [X] T022 Implement catalog list/download/verify in `src/letsaigc/models/`
- [X] T023 Implement ComfyUI API client in `src/letsaigc/comfy/client.py`
- [X] T024 Implement loopback launcher in `src/letsaigc/comfy/runtime.py`
- [X] T025 Add SD1.5 workflow UI/API/contract files in `workflows/`
- [X] T026 Add SDXL workflow UI/API/contract files in `workflows/`
- [X] T027 Implement workflow validation in `src/letsaigc/workflows/`

## Phase 5: Provenance, evaluation and training

- [X] T028 Implement provenance in `src/letsaigc/tracking/manifest.py`
- [X] T029 Implement optional MLflow tracking in `src/letsaigc/tracking/mlflow_store.py`
- [X] T030 Implement workflow execution in `src/letsaigc/workflows/runner.py`
- [X] T031 Implement evaluation in `src/letsaigc/evaluation.py`
- [X] T032 Implement trainer preflight/arguments in `src/letsaigc/training/`
- [X] T033 Implement adapter records in `src/letsaigc/training/adapters.py`
- [X] T034 Implement gated export in `src/letsaigc/exporter.py`
- [X] T035 Implement neutral runpacks in `src/letsaigc/runpack.py`
- [X] T036 Implement public commands and `--json` in `src/letsaigc/cli.py`

## Phase 6: Bootstrap and storage

- [X] T037 [P] Define three isolated environments in `environment/`
- [X] T038 Implement component bootstrap in `scripts/bootstrap.ps1`
- [X] T039 Implement smoke orchestration in `scripts/smoke.ps1`
- [X] T040 Configure local DVC filesystem remote in `.dvc/config`
- [X] T041 Add dataset/artifact/adapter ownership documentation

## Phase 7: Automated verification

- [X] T042 [P] Test schemas/configuration in `tests/contract/`
- [X] T043 [P] Test ignore/safe-file policy in `tests/contract/`
- [X] T044 [P] Test diagnostics/model hashing in `tests/unit/`
- [X] T045 [P] Test license/export denials in `tests/unit/`
- [X] T046 [P] Test workflow validation in `tests/unit/`
- [X] T047 [P] Test manifests/runpacks in `tests/unit/`
- [X] T048 Test ComfyUI lifecycle with a fake server in `tests/integration/`
- [X] T049 Run complete core tests and package checks

## Phase 8: Local deployment and empirical acceptance

- [X] T050 Bootstrap and lock `letsaigc-core`
- [X] T051 Bootstrap and lock `letsaigc-comfy` and ComfyUI
- [X] T052 Bootstrap and lock `letsaigc-train-sdxl` and sd-scripts
- [X] T053 Verify DVC push/pull and MLflow queryability
- [X] T054 Synchronize/hash-verify SD1.5
- [X] T055 Synchronize/hash-verify SDXL
- [X] T056 Synchronize/smoke FLUX.1 Schnell FP8 when obtainable
- [X] T057 Run endpoint and loopback acceptance
- [X] T058 Run fixed-seed SD1.5 and SDXL inference smoke tests
- [X] T059 Run four-case evaluation and record timing/VRAM/reviews
- [X] T060 Run SDXL LoRA 512 px/20-step smoke and reload adapter
- [X] T061 Backfill evidence and blockers in local evaluation report

## Dependencies

T006–T012 follow T001–T005. T018–T020 follow T013–T017. T021–T027 follow
the public types. T028–T036 follow runtime contracts. Empirical acceptance is
ordered T050 -> T051/T052 -> T053–T056 -> T057–T060 -> T061.
