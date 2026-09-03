# Implementation Plan: Constrained Comfy Workflow Compiler

**Feature ID**: `011-constrained-comfy-compiler` | **Date**: 2026-09-02 | **Spec**: [spec.md](spec.md)

## Summary

Compile allowlisted recipe blocks to task-local API graphs, upload verified inputs,
validate live native nodes and bounds, and execute through a Comfy backend adapter.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic, httpx, existing Comfy client/runner  
**Storage**: Committed recipes; compiled graphs in `.local/agent`  
**Testing**: pytest fake `/object_info`, upload, prompt/history  
**Target Platform**: ComfyUI 0.34.2 on loopback  
**Project Type**: Restricted graph compiler  
**Performance Goals**: Validate before GPU queue submission  
**Constraints**: Native nodes only; existing static workflows unchanged  
**Scale/Scope**: Four initial recipe families  
**License Lanes**: Derived from catalog entries  
**Resource Budget**: Approved recipe bounds  
**Provenance**: Recipe/graph/model/input/output hashes  
**Approval Boundary**: Recipe and maxima immutable; tunables allowlisted  
**Credential and Input Safety**: Only verified session images may be uploaded

## Constitution Check

No arbitrary Agent graph or custom node is executable; model, resource, output,
and provenance checks precede queueing.

## Project Structure

```text
configs/workflows/recipes/*.yaml
src/letsaigc/workflows/compiler.py
src/letsaigc/backends/comfy.py
src/letsaigc/comfy/client.py
tests/{unit,contract}/test_workflow_compiler.py
```

**Structure Decision**: Reuse existing static graph assets as trusted recipe blocks
where possible; compile image-to-image as a bounded native-node transform.

## Complexity Tracking

No constitution violations.
