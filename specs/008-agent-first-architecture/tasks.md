# Tasks: Agent-first Architecture

## Phase 1: Setup
- [x] T001 Update Constitution 2.0.0 and dependent templates in `.specify/`
- [x] T002 Add historical boundary note to `specs/001-project-harness/spec.md`

## Phase 2: User Story 1 - Product Boundary
- [x] T003 [US1] Update product description in `README.md` and `pyproject.toml`
- [x] T004 [US1] Update CLI root help in `src/letsaigc/cli.py`
- [x] T005 [US1] Document control-plane boundaries in `README.md`

## Phase 3: User Story 2 - Compatibility
- [x] T006 [US2] Preserve expert command registrations in `src/letsaigc/cli.py`
- [x] T007 [US2] Add CLI and boundary regression tests in `tests/contract/test_agent_cli.py`

## Phase 4: Polish
- [x] T008 Verify README links, CLI help, pytest, and ruff from repository root

## Dependencies & Strategy
T001-T002 precede user stories. US1 defines terminology; US2 verifies compatibility.

