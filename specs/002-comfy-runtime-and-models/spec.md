# Feature Specification: Comfy Runtime and Models

**Feature Branch**: `002-comfy-runtime-and-models`  
**Created**: 2026-08-31  
**Status**: Implemented

## Goal

Provide an isolated, loopback-only ComfyUI runtime and a governed local model
catalog for repeatable 2D game-asset inference on the RTX 3080 workstation.

## User Stories

1. As an operator, I can bootstrap the pinned ComfyUI source and Python/CUDA
   environment without modifying global Python, PowerShell policy, or CUDA Toolkit.
2. As an asset creator, I can synchronize only safe, licensed, revision-pinned
   model profiles and reject missing, altered, gated, restricted, or unsafe files.
3. As an automation client, I can use health, prompt, WebSocket, and history APIs
   on `127.0.0.1` and reproduce fixed-seed SD1.5, SDXL, and Schnell runs.

## Requirements and Acceptance

- ComfyUI and its PyTorch environment MUST be version-locked and independently
  rebuildable; third-party custom nodes MUST be absent in v1.
- Model entries MUST declare source revision, SHA-256, license lane, gated state,
  safe format, profiles, and resource budgets.
- The server MUST reject non-loopback binding through the project launcher.
- Acceptance requires `/system_stats`, `/object_info`, `/prompt`, WebSocket and
  `/history/{id}`, plus fixed-seed SD1.5 512px, SDXL 1024px and Schnell FP8 smoke.
- SD3.5 Medium remains conditional/gated; FLUX.1 Dev remains restricted and is not
  downloaded by default. No tool may accept an agreement on the user's behalf.

## Out of Scope

Custom nodes, public/multi-user serving, video/3D models, and model fine-tuning.
