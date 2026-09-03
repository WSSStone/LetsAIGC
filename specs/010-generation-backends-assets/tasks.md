# Tasks: Generation Backends and Assets

## Phase 1: Setup
- [x] T001 Add provider and pricing configs in `configs/providers/`
- [x] T002 Add backend package in `src/letsaigc/backends/`

## Phase 2: User Story 1 - Safe Inputs
- [x] T003 [US1] Add SSRF/media security tests in `tests/unit/test_asset_resolver.py`
- [x] T004 [US1] Implement local/HTTPS resolution in `src/letsaigc/assets/resolver.py`

## Phase 3: User Story 2 - Routing
- [x] T005 [US2] Add local-first capability tests in `tests/unit/test_generation_router.py`
- [x] T006 [US2] Implement capabilities and router in `src/letsaigc/generation/router.py`

## Phase 4: User Story 3 - OpenAI Images
- [x] T007 [US3] Add generate/edit/redaction/usage tests in `tests/contract/test_openai_backend.py`
- [x] T008 [US3] Implement dated cost ledger in `src/letsaigc/generation/pricing.py`
- [x] T009 [US3] Implement pinned image adapter in `src/letsaigc/backends/openai_image.py`
- [x] T010 [US3] Add key readiness to `src/letsaigc/doctor.py`

## Phase 5: Polish
- [x] T011 Verify fake integrations, stale-price failure, pytest, and ruff
- [ ] T012 [LIVE] User-budgeted GPT Image 2 generation/edit; record request IDs, usage and actual cost

## Dependencies & Strategy
Resolved assets and capability routing precede provider execution.
