# Feature Specification: Workflow Runner and Evaluation

**Feature Branch**: `003-workflow-runner-and-evaluation`  
**Created**: 2026-08-31  
**Status**: Implemented; human production approval remains operator-owned

## Goal

Turn editable ComfyUI graphs into validated, traceable executions and a repeatable
2D game-asset evaluation lane with fail-closed production export.

## User Stories

1. As an engineer, I can run a workflow by ID with schema-validated overrides and
   receive a machine-readable result using `--json`.
2. As an evaluator, I can run character, icon, texture and style-consistency cases
   with fixed seeds and query their MLflow experiment record.
3. As a production owner, I can inspect complete model/workflow/adapter/output hashes,
   approve a run explicitly, or receive exact export denial reasons.

## Requirements and Acceptance

- Every exposed graph MUST have UI JSON, API JSON and a strict contract.
- Each run MUST retain state, inputs, revisions/hashes, environment, output hashes,
  Comfy prompt ID and MLflow ID; failures MUST also remain visible.
- Export MUST require successful status, all validations, production license lanes,
  verified output hashes and explicit human approval.
- Runpacks MUST remain provider-neutral and exclude third-party weights, outputs and
  secrets while including required contracts, configuration and pointer metadata.
- Acceptance includes contract/unit/fake-server integration tests and the four-case
  local evaluation. Visual scores and promotion remain human decisions.
