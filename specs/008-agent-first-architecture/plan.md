# Implementation Plan: Agent-first Architecture

**Feature ID**: `008-agent-first-architecture` | **Date**: 2026-09-02 | **Spec**: [spec.md](spec.md)

## Summary

Correct product terminology and formalize dependency boundaries without moving
stable runtime modules or changing expert CLI contracts.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Typer, Pydantic, existing runtime stack  
**Storage**: Git documentation and existing `.local` runtime boundary  
**Testing**: pytest, ruff, CLI help regression  
**Target Platform**: Windows 11 local CLI  
**Project Type**: CLI workbench  
**Performance Goals**: No runtime overhead  
**Constraints**: Backward-compatible expert commands; no bulk module moves  
**Scale/Scope**: Repository terminology, governance, and architecture  
**License Lanes**: Existing policies unchanged  
**Resource Budget**: Documentation-only change  
**Provenance**: Git revision and test evidence  
**Approval Boundary**: Defined as a product invariant; implemented in feature 009  
**Credential and Input Safety**: Defined as a product invariant; implemented in feature 010

## Constitution Check

- Development Harness and product control planes are separated.
- Existing product invariants remain enforceable and gain Agent-specific rules.
- No model, paid provider, GPU execution, or runtime service is invoked.

## Project Structure

```text
.agents/ .specify/ AGENTS.md specs/   # Development Harness
src/letsaigc/ configs/ workflows/     # Product control plane
tests/ README.md pyproject.toml        # Product verification and entry docs
```

**Structure Decision**: Preserve existing runtime layout; add new Agent domains in
later features and describe one-way dependencies in committed documentation.

## Complexity Tracking

No constitution violations.
