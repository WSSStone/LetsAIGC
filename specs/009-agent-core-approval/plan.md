# Implementation Plan: Agent Core and Approval

**Feature ID**: `009-agent-core-approval` | **Date**: 2026-09-02 | **Spec**: [spec.md](spec.md)

## Summary

Add strict Agent/session schemas, canonical fingerprints, local atomic persistence,
budget accounting, Responses orchestration, sequential allowlisted dispatch, and CLI states.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Typer, OpenAI Python SDK (lazy runtime import)  
**Storage**: `.local/agent/sessions` and `.local/agent/tasks` JSON  
**Testing**: pytest fakes; no network/GPU  
**Target Platform**: Windows 11 local CLI  
**Project Type**: Stateful CLI orchestrator  
**Performance Goals**: State persisted after every accepted transition  
**Constraints**: `store=false`, `parallel_tool_calls=false`, revisions <=10  
**Scale/Scope**: One user, one sequential tool call at a time  
**License Lanes**: Preserved in plans and manifests  
**Resource Budget**: Explicit USD/GPU caps; no live calls in automated tests  
**Provenance**: Task ledger, request/tool records, fingerprints  
**Approval Boundary**: Canonical immutable envelope with SHA-256 fingerprint  
**Credential and Input Safety**: Model client receives only resolved, derived assets

## Constitution Check

- Planning and execution are separate, budgeted, and reproducible.
- Agent authority excludes all prohibited operations.
- Local session persistence never depends on Responses server storage.

## Project Structure

```text
src/letsaigc/agent/{models,storage,budget,responses,orchestrator}.py
src/letsaigc/generation/{models,router}.py
tests/{unit,contract,integration}/test_agent_*.py
```

**Structure Decision**: New domains depend on existing deterministic services;
existing expert services do not depend on Agent code.

## Complexity Tracking

No constitution violations.
