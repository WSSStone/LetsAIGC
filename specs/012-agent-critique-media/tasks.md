# Tasks: Agent Critique and Media Orchestration

## Phase 1: Setup
- [x] T001 Extend compatible RunManifest schemas in `src/letsaigc/schemas/models.py`
- [x] T002 Extend manifest writing/tracking in `src/letsaigc/tracking/manifest.py`

## Phase 2: User Story 1 - Critique and Revision
- [x] T003 [US1] Add hard-check/score/revision tests in `tests/unit/test_agent_critic.py`
- [x] T004 [US1] Implement evaluation in `src/letsaigc/agent/critic.py`
- [x] T005 [US1] Implement bounded revision loop in `src/letsaigc/agent/orchestrator.py`

## Phase 3: User Story 2 - Video Evidence
- [x] T006 [US2] Add contact-sheet tests in `tests/unit/test_contact_sheet.py`
- [x] T007 [US2] Implement deterministic contact sheets in `src/letsaigc/media/contact_sheet.py`

## Phase 4: User Story 3 - Media Tools
- [x] T008 [US3] Add Sprite/drama authority and lineage tests in `tests/integration/test_agent_guardrails.py`
- [x] T009 [US3] Add deterministic media adapters in `src/letsaigc/backends/media.py`
- [x] T010 [US3] Integrate parent/child evidence in `src/letsaigc/agent/orchestrator.py`

## Phase 5: Polish
- [x] T011 Update `README.md`, `.env.example`, and CLI help
- [x] T012 Run full pytest/ruff regression and document unexecuted live acceptance
- [ ] T013 [LIVE] Demonstrate one real multimodal critique/revision within a user-approved budget

## Dependencies & Strategy
Manifest compatibility precedes loop evidence; critique precedes media wrappers.

## Implementation clarification (2026-09-03)

Sprite/drama are separately approved derived tasks. The Agent drama adapter accepts
recorded shots, not embedded workflow jobs that could escape its postprocessing budget.
Interrupted provider work is not automatically replayed; uncertain consumption remains
in `unsettled_*` until operator reconciliation. See `docs/agent-quickstart.md`.
