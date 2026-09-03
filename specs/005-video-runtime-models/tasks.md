# Tasks: Video Runtime and Models

**Input**: Design documents in `specs/005-video-runtime-models/`  
**Tests**: Contract, failure-path, provenance, and regression tests precede implementation.

## Phase 1: Setup

- [x] T001 Add Pillow media dependency and temporal-budget prompts in `pyproject.toml` and `.specify/templates/{spec,plan,tasks}-template.md`
- [x] T002 Add Wan model profiles and video job/import configuration examples in `configs/models/catalog.yaml` and `configs/video/`
- [x] T003 Add native Wan2.1 UI/API/contract workflow assets in `workflows/`

## Phase 2: Foundational Tests and Schemas

- [x] T004 [P] Add schema and backward-compatibility tests in `tests/contract/test_video_contracts.py`
- [x] T005 [P] Add FFmpeg probe, unsafe path, and capability tests in `tests/unit/test_media.py`
- [x] T006 [P] Add video import and runpack round-trip tests in `tests/unit/test_video_runpack.py`
- [x] T007 Extend media, temporal-budget, import/job, output, and manifest schemas in `src/letsaigc/schemas/models.py`

## Phase 3: User Story 1 - Generate a tracked local video (P1)

**Independent Test**: A declared video history result becomes a probed, hashed `video_generation` output while legacy image discovery remains unchanged.

- [x] T008 [US1] Implement safe FFmpeg discovery, capability checks, command execution and probe normalization in `src/letsaigc/media/tools.py`
- [x] T009 [US1] Implement declared media output discovery and path validation in `src/letsaigc/media/artifacts.py`
- [x] T010 [US1] Generalize workflow execution and manifest evidence in `src/letsaigc/workflows/runner.py` and `src/letsaigc/tracking/manifest.py`
- [x] T011 [US1] Add FFmpeg diagnostics in `src/letsaigc/doctor.py`
- [x] T012 [US1] Add the `video run` CLI facade in `src/letsaigc/cli.py`

## Phase 4: User Story 2 - Import external video (P2)

**Independent Test**: A valid external clip is copied, probed, hashed and governed; incomplete provenance cannot be production-exported.

- [x] T013 [US2] Implement tracked video import and failed-run persistence in `src/letsaigc/media/importer.py`
- [x] T014 [US2] Add the `video import` CLI command in `src/letsaigc/cli.py`
- [x] T015 [US2] Extend export policy coverage for imported and derived media in `src/letsaigc/policy/gates.py`

## Phase 5: User Story 3 - Provider-neutral runpack (P3)

**Independent Test**: A job runpack contains no secret/weight/absolute paths and only a matching result can be ingested.

- [x] T016 [US3] Implement canonical video-job fingerprinting and runpack build/ingest in `src/letsaigc/runpack.py`
- [x] T017 [US3] Add job build and result ingest CLI forms in `src/letsaigc/cli.py`
- [x] T018 [US3] Log selected media and lineage evidence through `src/letsaigc/tracking/mlflow_store.py`

## Phase 6: Polish and Validation

- [x] T019 Run schema, unit, contract and integration tests and fix regressions in `tests/`
- [x] T020 Update operator guidance and local feasibility evidence in `README.md` and `.doc/video-aigc-feasibility-and-evaluation.md`
- [x] T021 Validate the quickstart and record deferred GPU/model smoke evidence in `specs/005-video-runtime-models/quickstart.md`

## Dependencies

- Setup precedes schema implementation.
- T004–T006 are written before T007–T018.
- US1 provides the media boundary required by US2 and US3.
- GPU smoke is a documented environment-dependent acceptance step; CPU tests must pass without model downloads.
