# Feature Specification: Short Drama Orchestration

**Feature ID**: `007-short-drama-orchestration`  
**Created**: 2026-08-31  
**Status**: Approved for implementation

## User Scenarios & Testing

### User Story 1 - Render a visual shot project (Priority: P1)

As a short-drama producer, I can define target media properties and an ordered shot list, then receive one normalized playable video with project and shot lineage.

**Independent Test**: Render two tracked fixture clips at one resolution/FPS and verify parent/shot source relationships and final media properties.

### User Story 2 - Attach supplied sound and subtitles (Priority: P2)

As an editor, I can mux an external audio file and retain or explicitly burn an SRT subtitle file while both inputs remain hashed and traceable.

**Independent Test**: Render two clips with WAV and SRT, then verify the final video, sidecar subtitle, input hashes, and successful manifest.

### User Story 3 - Resume an interrupted project (Priority: P3)

As an operator, I can reuse normalized shots only when their source and project fingerprints match, while changed or failed shots are rebuilt independently.

**Independent Test**: Render twice with resume enabled, observe cache reuse, then change a shot hash and verify only that shot is rebuilt.

### Edge Cases

- A source run failed, has no video, changed after hashing, or has a restricted license.
- Shots have incompatible dimensions, frame rates, duration, codec, or corrupt frames.
- Audio is shorter or longer than the visual timeline.
- Subtitle file is missing or burn-in fails on a path requiring escaping.
- A project contains duplicate shot identifiers, no shots, or invalid fade durations.

## Requirements

- **FR-001**: A project MUST declare positive output width, height, FPS and at least one uniquely identified ordered shot.
- **FR-002**: Each shot MUST reference exactly one tracked run or inline local video workflow.
- **FR-003**: Every shot MUST be normalized to project resolution, FPS, H.264 and yuv420p before assembly.
- **FR-004**: Hard cuts are default; explicit fade transitions MUST declare a positive duration shorter than adjacent shots.
- **FR-005**: External audio MUST be padded or trimmed to the visual duration and encoded as AAC; external SRT MUST be hashed and copied, with burn-in only when explicitly requested.
- **FR-006**: Project and shot fingerprints MUST support safe resume without accepting changed sources.
- **FR-007**: The parent `drama_render` manifest MUST link all child/source runs and input hashes, preserve license lanes, record FFmpeg commands, and log selected outputs to MLflow.
- **FR-008**: The output MUST be checked for target dimensions/FPS, positive duration, decodability and sustained black segments.
- **FR-009**: Character/style continuity remains an explicit human-review gate rather than an automated production claim.

## Success Criteria

- **SC-001**: Two fixture shots plus WAV and SRT produce one playable target-resolution MP4 and a matching subtitle sidecar.
- **SC-002**: 100% of shot, audio, subtitle and final outputs carry verified hashes and parent/source lineage.
- **SC-003**: Re-running an unchanged project with resume reuses every normalized shot; changing one source invalidates only its cache entry.
- **SC-004**: Missing/corrupt sources, invalid fades, media mismatch, black-segment failures and hash tampering prevent successful production status.

## Assumptions

- v1 is a visual-shot pipeline; TTS, music generation, lip sync and automated character-consistency scoring are out of scope.
- High-quality cloud shots enter through tracked imports/runpacks before drama assembly.
- Project files explicitly declare aspect and FPS; no implicit landscape/vertical default is used.
