# Tasks: Short Drama Orchestration

- [x] T001 Add drama project and shot schemas in `src/letsaigc/schemas/models.py`
- [x] T002 [P] Add two-shot WAV/SRT integration test in `tests/integration/test_drama_pipeline.py`
- [x] T003 [P] Add project validation and fingerprint unit tests in `tests/unit/test_drama.py`
- [x] T004 [US1] Implement shot source resolution and normalization in `src/letsaigc/drama/pipeline.py`
- [x] T005 [US3] Implement content-addressed resume cache in `src/letsaigc/drama/cache.py`
- [x] T006 [US1] Implement cut/fade assembly and final media checks in `src/letsaigc/drama/pipeline.py`
- [x] T007 [US2] Implement external audio padding/mux and SRT sidecar/burn handling in `src/letsaigc/drama/pipeline.py`
- [x] T008 [US1] Record parent/child lineage and MLflow evidence in `src/letsaigc/drama/pipeline.py`
- [x] T009 Add `drama render --project [--resume]` in `src/letsaigc/cli.py`
- [x] T010 Add project example and run full regressions in `configs/drama/` and `tests/`
