# Implementation Plan: Comfy Runtime and Models

Use `letsaigc-comfy` (Python 3.12, PyTorch cu130) and a pinned ComfyUI checkout in
`.local/runtime/ComfyUI`. Generate `extra_model_paths.yaml` so all weights remain
under `.local/models`. The core CLI owns doctor, catalog sync/verification, launch,
API submission and manifests; ComfyUI owns graph execution only.

Constitution gates pass: hashes and revisions provide reproducibility; license lanes
segregate promotion; all services bind to loopback; workflows remain committed text;
base weights and runtime state remain ignored.

Implementation evidence is recorded in the local feasibility report and manifests.
