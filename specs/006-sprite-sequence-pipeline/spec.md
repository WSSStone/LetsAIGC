# Feature Specification: Sprite Sequence Pipeline

**Feature ID**: `006-sprite-sequence-pipeline`  
**Created**: 2026-08-31  
**Status**: Approved for implementation

## User Scenarios & Testing

### User Story 1 - Build an engine-neutral sprite sequence (Priority: P1)

As an asset producer, I can turn a tracked video or development clip into normalized RGBA frames, a PNG sprite sheet, and frame metadata.

**Independent Test**: Process a deterministic green-screen moving subject and obtain 512×512 RGBA frames, a valid atlas, JSON metadata, and a derived run manifest.

### User Story 2 - Detect unusable temporal assets (Priority: P2)

As a reviewer, I receive explicit failures or warnings for empty frames, background residue, clipping, anchor drift, inconsistent sizes, excessive frame counts, or oversized atlases.

**Independent Test**: Feed fixtures that violate each threshold and verify production validation is blocked without hiding diagnostics.

### User Story 3 - Promote a reviewed sprite artifact (Priority: P3)

As a producer, I can approve and export only a fully traced, hashed, validated sequence whose source license permits production.

**Independent Test**: A tracked production-lane source passes automated gates but remains blocked until human approval; direct untracked input remains development-only.

### Edge Cases

- Subject colors overlap the selected key color or every pixel becomes transparent.
- Source FPS is missing or variable and extraction produces zero or too many frames.
- The detected subject touches the crop/canvas edge or cannot fit the configured atlas.
- A jump/flying animation needs a manual or fixed anchor instead of bottom-center stabilization.
- A frame changes after sheet creation or metadata references a missing frame.

## Requirements

- **FR-001**: The pipeline MUST accept exactly one tracked source run or direct development input.
- **FR-002**: It MUST extract at a configured FPS and cap the result at the configured maximum frame count.
- **FR-003**: It MUST create Alpha using configurable Lab-distance color keying, despill, and edge feathering.
- **FR-004**: It MUST use one common scale across frames, preserve aspect ratio, and place the detected bottom-center anchor at the configured canvas location.
- **FR-005**: It MUST validate foreground coverage, border contact, dimensions, mode, anchor error, hashes, frame count, and atlas bounds.
- **FR-006**: It MUST generate a row-major near-square RGBA PNG atlas and engine-neutral JSON containing frame rectangles, durations, anchors, source hashes, and profile identity.
- **FR-007**: Every derived output MUST link to the source run/output hash and be recorded in MLflow and a `sprite_pipeline` manifest.
- **FR-008**: Production export MUST require a production-lane tracked source, successful automatic checks, valid hashes, and explicit human approval.
- **PER-001**: The default profile is `general-rgba-512`: 512×512, 12 FPS, at most 24 frames, key `#00FF00`, Lab thresholds 18/45, despill 0.75, 1 px feather, anchor `(0.5, 0.95)`, and atlas at most 8192×8192.
- **PER-002**: Raw videos and intermediate frames remain under `.local`; committed data consists of profiles, schemas, tests, metadata examples and DVC pointers.

## Success Criteria

- **SC-001**: The deterministic fixture produces non-empty 512×512 RGBA frames whose calibrated anchors are within 1 px of target.
- **SC-002**: Every output frame, atlas, and metadata file has a verified SHA-256 and source relationship.
- **SC-003**: All specified invalid coverage, border, frame-count, atlas and tamper cases block production.
- **SC-004**: A consumer can reconstruct frame order, rectangle, duration and anchor using only the PNG and JSON.

## Assumptions

- General RGBA art is the production default; a secondary pixel-art profile uses nearest-neighbor and hard Alpha.
- Bottom-center stabilization is appropriate for grounded characters; profiles may select fixed/manual anchors for other asset types.
- Color keying is the v1 baseline; ML matting is out of scope.
