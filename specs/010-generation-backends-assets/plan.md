# Implementation Plan: Generation Backends and Assets

**Feature ID**: `010-generation-backends-assets` | **Date**: 2026-09-02 | **Spec**: [spec.md](spec.md)

## Summary

Implement a hardened asset resolver, common backend protocol/capability router,
pinned OpenAI image adapter, and dated cost reservation/settlement.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: httpx, Pillow, Pydantic, OpenAI SDK  
**Storage**: `.local/agent/sessions/<id>/inputs` and task outputs  
**Testing**: pytest with custom transports and fake providers  
**Target Platform**: Windows 11 local CLI  
**Project Type**: Backend adapter and asset-security library  
**Performance Goals**: Reject invalid assets before model/provider calls  
**Constraints**: HTTPS only, 25 MiB, 3 redirects, 30 seconds  
**Scale/Scope**: Several images per local single-user task  
**License Lanes**: Provider entry is production; model terms remain explicit  
**Resource Budget**: Worst-case reservation before paid calls  
**Provenance**: Input/output hashes, provider request ID, usage and price version  
**Approval Boundary**: Provider, snapshot, size, quality, background and count fixed  
**Credential and Input Safety**: SSRF/magic/decode defense and total secret redaction

## Constitution Check

All remote calls are approved, budgeted, pinned, and recorded. Input and credential
boundaries fail closed. Remote video is absent.

## Project Structure

```text
src/letsaigc/assets/resolver.py
src/letsaigc/backends/{base,openai_image}.py
src/letsaigc/generation/{router,pricing}.py
configs/providers/{models,pricing}.yaml
tests/{unit,contract}/test_{assets,openai_backend,routing}.py
```

**Structure Decision**: Provider code accepts only verified `ResolvedAsset` bytes.

## Complexity Tracking

No constitution violations.
