# Tasks: Agent Core and Approval

## Phase 1: Setup
- [x] T001 Add optional OpenAI/runtime configuration in `pyproject.toml` and `.env.example`
- [x] T002 Create Agent/generation package boundaries in `src/letsaigc/agent` and `src/letsaigc/generation`

## Phase 2: Foundational
- [x] T003 [P] Add Agent, plan, budget, approval, and task schemas in `src/letsaigc/schemas/agent.py`
- [x] T004 [P] Add atomic local state store in `src/letsaigc/agent/storage.py`
- [x] T005 Add canonical approval and budget ledger logic in `src/letsaigc/agent/approval.py`

## Phase 3: User Story 1 - Plan
- [x] T006 [US1] Add fake-first Responses planner contract tests in `tests/contract/test_responses_contract.py`
- [x] T007 [US1] Implement Responses adapter with `store=false` in `src/letsaigc/agent/responses.py`
- [x] T008 [US1] Implement planning state transitions in `src/letsaigc/agent/orchestrator.py`

## Phase 4: User Story 2 - Execute
- [x] T009 [US2] Test stale fingerprints, budgets, and tool authority in `tests/unit/test_agent_approval.py`
- [x] T010 [US2] Implement sequential allowlisted dispatch and execution in `src/letsaigc/agent/orchestrator.py`

## Phase 5: User Story 3 - CLI and Resume
- [x] T011 [US3] Add `agent` command group in `src/letsaigc/cli.py`
- [x] T012 [US3] Verify local state recovery in `tests/integration/test_agent_flow.py`

## Phase 6: Polish
- [x] T013 Verify JSON non-interactivity, pytest, ruff, and no secret serialization

## Dependencies & Strategy
Schemas/store/approval block planning; planning blocks execution; CLI wraps both.
