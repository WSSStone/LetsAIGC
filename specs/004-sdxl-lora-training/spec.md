# Feature Specification: SDXL LoRA Training

**Feature Branch**: `004-sdxl-lora-training`  
**Created**: 2026-08-31  
**Status**: Implemented smoke baseline; production adapter approval pending human review

## Goal

Train an SDXL UNet-only LoRA within 10 GiB VRAM, preserve dataset and experiment
lineage, reject numerically invalid adapters, and prove the result loads in ComfyUI.

## User Stories

1. As a trainer, I can run a fixed 512px/20-step smoke profile or start from a 768px
   low-VRAM template without contaminating the core/Comfy environments.
2. As an auditor, I can trace an adapter to base-model hash, DVC dataset revision,
   trainer commit, full effective command, configuration hash and MLflow run.
3. As a workflow author, I can load the verified adapter using a committed ComfyUI
   workflow contract whose manifest records the adapter SHA-256.

## Requirements and Acceptance

- Training MUST use Python 3.10, pinned sd-scripts, batch 1, rank/alpha 8, UNet-only,
  gradient checkpointing, SDPA, caches, AdamW8bit and safe output serialization.
- Source data MUST be DVC-versioned; disk caches MUST be written to local staging,
  not mutate the tracked dataset.
- VAE encoding MUST remain numerically stable on this checkpoint (`no_half_vae`).
- A completed trainer process is insufficient: all tensors MUST be finite and LoRA
  up weights MUST contain actual updates before registration.
- Acceptance requires 20 finite-loss steps, a hashed AdapterRecord with DVC revision
  and MLflow ID, and successful ComfyUI `LoraLoader` inference.

## Out of Scope

FLUX LoRA, full-model fine-tuning, production quality claims, and cloud-provider
binding. The four-image smoke dataset validates plumbing, not artistic generalization.
