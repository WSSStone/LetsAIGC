# Implementation Plan: Agent Critique and Media Orchestration

**Feature ID**: `012-agent-critique-media` | **Date**: 2026-09-02 | **Spec**: [spec.md](spec.md)

## Summary

Complete the Agent loop with multimodal critique, deterministic media evidence,
bounded revision, existing Sprite/drama adapters, MLflow nesting, and manifest 1.2.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: OpenAI SDK, Pillow, system FFmpeg/ffprobe, MLflow adapter  
**Storage**: `.local/agent`, `.local/runs`, existing MLflow store  
**Testing**: pytest fakes and tiny deterministic media fixtures  
**Target Platform**: Windows 11 CLI  
**Project Type**: Agent orchestration and evaluation  
**Performance Goals**: Hard checks before paid critic revision; bounded loop  
**Constraints**: Score threshold 0.8; at most 10 revisions  
**Scale/Scope**: One sequential task and candidate per iteration  
**License Lanes**: Inherited and fail-closed  
**Resource Budget**: Generation, evaluation, contact-sheet, and postprocess accounted  
**Provenance**: RunManifest 1.2 and nested MLflow lineage  
**Approval Boundary**: Only declared revision fields mutable  
**Credential and Input Safety**: Derived local images only; no raw URL retrieval by model

## Constitution Check

Hard constraints outrank critic score; all loops are budgeted and evidenced; Agent
tools remain constrained and promotion remains human-only.

## Project Structure

```text
src/letsaigc/agent/{critic,orchestrator}.py
src/letsaigc/media/contact_sheet.py
src/letsaigc/backends/media.py
src/letsaigc/schemas/{agent,models}.py
tests/{unit,contract,integration}/test_agent_*.py
```

**Structure Decision**: Wrap existing media services; do not duplicate or relocate them.

## Complexity Tracking

No constitution violations.
