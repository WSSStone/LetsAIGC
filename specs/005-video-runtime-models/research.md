# Research: Video Runtime and Models

## Decision: Wan2.1 T2V 1.3B is the local hard baseline

**Rationale**: The official ComfyUI native workflow documents an approximately 8 GiB VRAM requirement, fits the 10 GiB machine, uses native nodes, and the upstream model is Apache-2.0.

**Alternatives considered**: Wan2.2 A14B and modern LTX 22B exceed the local baseline; older LTX 2B uses a conditional commercial license and is not needed for v1.

## Decision: Wan2.2 TI2V 5B remains experimental

**Rationale**: ComfyUI documents 8 GiB operation with offload while the upstream reference implementation documents a 24 GiB minimum. Only measured local evidence can resolve this deployment-specific difference.

**Alternatives considered**: Making I2V a v1 gate was rejected because it would make success dependent on an unverified resource claim.

## Decision: Use native ComfyUI workflows only

**Rationale**: The locked ComfyUI exposes native video creation, save, Wan latent, and sampling nodes. Avoiding third-party nodes preserves the existing security and lock policy.

## Decision: Use operator-managed system FFmpeg

**Rationale**: The user explicitly chose the registered command-line installation. Reproducibility comes from capability checks plus captured resolved path/version/build and normalized arguments, not from installing another environment.

**Alternatives considered**: A dedicated Mamba environment was more reproducible but contradicted the selected operating model; bundling a binary adds packaging and redistribution concerns.

## Decision: Declared output mappings supersede heuristic video scanning

**Rationale**: Comfy history payloads vary by output node. Explicit node/field/role/extensions prevent accidental collection and make output behavior contract-testable. Legacy image contracts retain their prior fallback.

## Decision: Runpacks use canonical request fingerprints

**Rationale**: Provider neutrality requires data contracts, not provider SDKs. A canonical JSON SHA-256 binds workflow, model identifiers, references, parameters and expected output constraints to imported result metadata without including credentials or weights.

