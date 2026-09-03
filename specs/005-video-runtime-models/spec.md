# Feature Specification: Video Runtime and Models

**Feature ID**: `005-video-runtime-models`  
**Created**: 2026-08-31  
**Status**: Approved for implementation  
**Input**: Extend LetsAIGC with reproducible local video generation, tracked media artifacts, system FFmpeg integration, and provider-neutral overflow execution.

## User Scenarios & Testing

### User Story 1 - Generate a tracked local video (Priority: P1)

As a workflow author, I can run a committed video workflow and receive a playable video whose model, workflow, parameters, environment, media properties, and output hashes are recorded.

**Why this priority**: Every downstream temporal asset depends on a reproducible video source.

**Independent Test**: Run the fixed local smoke workflow and verify that a playable 512×512, 17-frame result and a complete successful run record are produced.

**Acceptance Scenarios**:

1. **Given** the production-lane smoke model and native workflow are present, **When** the operator runs the video workflow with a fixed seed, **Then** a playable video and complete provenance record are produced.
2. **Given** a declared workflow output, **When** the runtime returns video or image media, **Then** only declared nodes, fields, roles, and extensions are collected.
3. **Given** a missing model, hash mismatch, unavailable service, insufficient disk, or failed generation, **When** a run is attempted, **Then** it fails visibly and cannot be exported as production.

---

### User Story 2 - Import an externally generated video (Priority: P2)

As an asset producer, I can register an existing video with explicit source, model, license, and revision metadata so that local post-processing uses the same provenance model as local generation.

**Why this priority**: The local GPU cannot run every production-quality model, but downstream processing must remain provider-neutral.

**Independent Test**: Import a small video with valid metadata and verify its media probe, source hash, license lane, and run record; omit metadata and verify production use is denied.

**Acceptance Scenarios**:

1. **Given** a readable video and complete metadata, **When** it is imported, **Then** an immutable source hash and successful tracked video run are created.
2. **Given** a direct file without provenance, **When** it is used for development, **Then** it is allowed only in the development lane and production export remains blocked.

---

### User Story 3 - Exchange a provider-neutral video runpack (Priority: P3)

As an operator, I can package a video job for stronger hardware without embedding provider secrets or model weights, then ingest the returned result with verifiable lineage.

**Why this priority**: It preserves a local-first workflow while providing a controlled path beyond 10 GiB VRAM.

**Independent Test**: Build a runpack from a job definition, inspect that it contains no secrets or weights, and ingest a fixture result whose hash and request fingerprint become linked records.

**Acceptance Scenarios**:

1. **Given** a valid video job, **When** a runpack is built, **Then** it contains workflow/configuration, reference hashes, resource requirements, model identifiers, and an output-result contract without credentials or weights.
2. **Given** a matching result and complete execution metadata, **When** it is ingested, **Then** the result is probed, hashed, and linked to the runpack request.
3. **Given** a mismatched request fingerprint or incomplete license metadata, **When** ingestion is attempted, **Then** it is rejected.

### Edge Cases

- The media tool is missing, reports an unsupported build, times out, or returns malformed probe output.
- A workflow reports a file outside the configured ComfyUI output directory or with an undeclared extension.
- A video has zero frames, invalid frame rate, an unsupported codec, or a frame count beyond its contract budget.
- A model is gated, unknown-license, unsafe-serialized, missing, or hash-mismatched.
- An imported or runpack result changes after it has been hashed.
- A legacy image workflow has no explicit output declaration and must retain its existing collection behavior.

## Requirements

### Functional Requirements

- **FR-001**: The system MUST run versioned native video workflows while preserving all existing image workflow behavior.
- **FR-002**: Video workflow contracts MUST declare media kind, output location, artifact role, accepted extensions, resolution, frame rate, frame count, duration, and resource budgets.
- **FR-003**: The system MUST collect only declared outputs for new media contracts and MUST reject unsafe paths or undeclared file types.
- **FR-004**: Every collected media artifact MUST include its content hash, size, role, MIME type, codec, pixel format, dimensions, frame count, frame rate, duration, and Alpha capability when available.
- **FR-005**: The system MUST verify user-provided `ffmpeg` and `ffprobe` commands before media processing and MUST capture their resolved paths, versions, and build capabilities.
- **FR-006**: The system MUST record parent/source lineage, external runtime versions, relevant GPU telemetry, normalized command arguments, and media metadata without exposing secrets.
- **FR-007**: Operators MUST be able to import external videos with explicit source, model, revision, license, and provider labels.
- **FR-008**: Direct untracked media inputs MUST remain development-only until registered with complete provenance and license evidence.
- **FR-009**: Operators MUST be able to build and ingest provider-neutral video runpacks whose request/result fingerprints are verified.
- **FR-010**: The local smoke profile MUST use Wan2.1 T2V 1.3B, native ComfyUI nodes, safe weights, and a fixed 512×512, 17-frame test.
- **FR-011**: Wan2.2 TI2V 5B MUST remain experimental until a local no-OOM run, hash verification, and explicit human approval are recorded.
- **FR-012**: A failed validation, probe, generation, license check, hash check, or resource check MUST leave a failed run record and MUST block production export.

### Policy and Evidence Requirements

- **PER-001**: Wan model files MUST record their upstream Apache-2.0 license, exact source revision, SHA-256, safe format, and promotion lane.
- **PER-002**: The supported local profile is Windows 11, RTX 3080 10 GiB, 64 GiB RAM; every video contract MUST declare VRAM, RAM, disk, timeout, dimensions, FPS, and frame budgets.
- **PER-003**: Acceptance evidence MUST include model/workflow/output hashes, effective inputs and seed, ComfyUI and repository revisions, media-tool identity, environment snapshot, MLflow identifier, status, and human review where production promotion is requested.
- **PER-004**: Third-party weights, imported originals, raw generated videos, temporary frames, secrets, and provider credentials MUST remain under ignored local storage; committed files are contracts, catalogs, schemas, specifications, tiny owned fixtures, and DVC pointers.

### Key Entities

- **Media Artifact**: A hashed image, video, audio, subtitle, frame sequence, or metadata output with media properties and a semantic role.
- **Video Job**: A provider-neutral request containing a workflow, inputs, model dependencies, resource budget, and expected media result.
- **Run Manifest**: A backward-compatible record of execution state, provenance, environment, governance, outputs, and parent/source lineage.
- **Video Import Metadata**: Source, provider, model, revision, license lane, usage statement, and optional originating runpack fingerprint.

## Success Criteria

### Measurable Outcomes

- **SC-001**: A fixed local smoke request produces exactly one playable 512×512, 17-frame video or a complete actionable failed record; it never yields an untracked partial success.
- **SC-002**: 100% of accepted media outputs have valid content hashes and complete required media metadata.
- **SC-003**: Every existing image workflow and its contract tests continue to pass after the media schema upgrade.
- **SC-004**: A runpack can be built and ingested without cloud credentials, bundled model weights, or provider-specific executable code.
- **SC-005**: All tested missing-tool, unsafe-path, malformed-media, license, hash, service, disk, and OOM failures are visible and blocked from production export.

## Assumptions

- The operator installs and exposes compatible `ffmpeg` and `ffprobe` commands on `PATH`; bootstrap reports but does not install them.
- ComfyUI remains loopback-only and uses native nodes; third-party custom nodes are out of scope.
- Wan2.1 T2V is the local acceptance baseline; Wan2.2 I2V is non-blocking exploration and larger models use runpacks.
- Video LoRA, long-form generation, TTS, music generation, lip sync, and provider-specific cloud adapters are out of scope.
- Hardware smoke testing requires model downloads and a running ComfyUI instance and is distinct from deterministic CPU-only tests.
