# Implementation Plan: SDXL LoRA Training

Use the isolated `letsaigc-train-sdxl` environment and pinned sd-scripts checkout.
The core runner verifies the production-lane SDXL checkpoint, stages the DVC dataset
under `.local/cache`, maps harness fields to trainer flags, and persists failed or
successful manifests. A standalone validator runs inside the training environment
before AdapterRecord creation. The verified file is copied to ComfyUI's local LoRA
path and exercised through a UI/API/contract workflow.

Constitution gates pass: resource limits are explicit, sources and hashes are fixed,
DVC/MLflow provide lineage, safetensors is mandatory, and promotion remains gated.
