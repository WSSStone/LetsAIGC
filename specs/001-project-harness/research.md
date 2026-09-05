# Research and Decisions

## Runtime isolation

**Decision**: Use three Conda environments invoked through `conda run --no-capture-output -n`.
Core and ComfyUI use Python 3.12; sd-scripts uses the conservative Python 3.10
Windows baseline. PyTorch wheels supply their CUDA runtime; `nvcc`, Docker, WSL and
a system CUDA Toolkit are not v1 dependencies.

## ComfyUI source and networking

**Decision**: Clone official ComfyUI under `.local/runtime/ComfyUI`, check out the
exact lock revision, generate `extra_model_paths.yaml`, install no Manager/custom
nodes, and bind only `127.0.0.1`. Native `/system_stats`, `/object_info`, `/prompt`,
`/history/{id}` and `/ws` endpoints cover the automation lifecycle.

## Model governance and ladder

**Decision**: Catalog every file by repository, immutable revision, path, SHA-256,
format, license lane, gate and profile. Download to `.part`, then promote after hash
validation; safetensors is the default accepted format. SD1.5 FP16 is the smoke
baseline; SDXL Base 1.0 is production/LoRA baseline; FLUX.1 Schnell FP8 is an
offload-dependent production candidate; SD3.5 Medium is conditional; FLUX.1 Dev is
catalog-only/restricted. Unknown licenses and pickle checkpoints are denied.

## Training baseline

**Decision**: Pin sd-scripts and provide UNet-only SDXL LoRA profiles using rank and
alpha 8, batch 1, FP16, gradient checkpointing, SDPA, latent/text caches, AdamW8bit
and 512–768 px buckets. Smoke is exactly 20 steps at 512 px. Effective arguments are
stored before launch and never silently downshift after OOM.

## Tracking and ownership

**Decision**: MLflow uses `.local/mlflow/mlflow.db` and local artifacts with its UI
on `127.0.0.1:5000`. DVC uses `.local/dvc-remote` for owned datasets and promoted
adapters only. Immutable JSON manifests are the portable source of truth.

## Rejected alternatives

- One Python environment: dependency collision and irreproducible upgrades.
- Docker/WSL-first: unnecessary for the present native RTX workstation.
- Baseline custom nodes: excessive supply-chain and lock surface.
- Git LFS for base weights: third-party binaries would enter repository history.
- Vendor-specific cloud jobs: premature coupling; export a neutral runpack.
- Local FLUX LoRA/full tuning acceptance: 10 GiB is not a dependable target.

Exact runtime revisions and hashes are resolved into committed locks/catalogs by
bootstrap/sync rather than copied from mutable web pages.
