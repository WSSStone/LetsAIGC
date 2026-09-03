# Tasks: Constrained Comfy Workflow Compiler

## Phase 1: Setup
- [x] T001 Add strict recipes under `configs/workflows/recipes/`
- [x] T002 Add recipe/compiled workflow schemas in `src/letsaigc/schemas/agent.py`

## Phase 2: User Story 1 - Image Recipes
- [x] T003 [US1] Add compiler allowlist/bounds tests in `tests/unit/test_workflow_compiler.py`
- [x] T004 [US1] Implement deterministic T2I/I2I compilation in `src/letsaigc/workflows/compiler.py`

## Phase 3: User Story 2 - Video Recipes
- [x] T005 [US2] Add stable/experimental video recipe tests in `tests/unit/test_workflow_compiler.py`
- [x] T006 [US2] Implement Wan T2V/I2V recipe selection in `src/letsaigc/workflows/compiler.py`

## Phase 4: User Story 3 - Runtime Validation
- [x] T007 [US3] Add upload/object-info contract tests in `tests/contract/test_comfy_agent_backend.py`
- [x] T008 [US3] Add safe image upload to `src/letsaigc/comfy/client.py`
- [x] T009 [US3] Implement Comfy adapter in `src/letsaigc/backends/comfy.py`

## Phase 5: Polish
- [x] T010 Verify compiler determinism, custom-node rejection, pytest, and ruff
- [ ] T011 [LIVE] SDXL T2I/I2I and Wan2.1 512x512/17-frame validation after explicit plan approval
- [ ] T012 [OPTIONAL LIVE] Wan2.2 5B I2V exploratory validation; not a release blocker

## Dependencies & Strategy
Recipes/schemas precede compiler; compiler precedes live runtime validation.
