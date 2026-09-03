# Data Model: Video Runtime and Models

## MediaOutputDeclaration

- `node_id`: Comfy API workflow node identifier.
- `history_field`: output payload list field such as `images`, `videos`, or `audio`.
- `role`: semantic role, unique within the contract.
- `media_kind`: `image`, `video`, `audio`, `subtitle`, `frames`, or `metadata`.
- `allowed_extensions`: non-empty lowercase allowlist.
- `required`: missing required outputs fail the run.

## TemporalBudget

- Optional positive `width`, `height`, `fps`, `frame_count`, `duration_seconds`, and `temporary_disk_gib`.
- Probe results exceeding declared maxima fail contract validation.

## MediaMetadata

- `mime_type`, optional `codec`, `pixel_format`, `width`, `height`, `frame_count`, `fps`, `duration_seconds`, `has_alpha`.
- Rational FPS values are normalized to a positive decimal value.

## RunOutput 1.1

- Existing `path`, `sha256`, and `size_bytes` remain required/compatible.
- Optional `role`, `media_kind`, `media`, `derived_from_run_id`, and `derived_from_sha256` provide semantics and lineage.

## RunManifest 1.1

- Adds kinds `video_generation`, `sprite_pipeline`, and `drama_render`.
- Adds optional `parent_run_id` and `source_artifacts`.
- State transitions remain `created → validated → queued/running → succeeded|failed|cancelled`; imported media uses `created → validated → succeeded`.
- Old 1.0 manifests remain readable and are not rewritten until explicitly saved.

## VideoImportMetadata

- Required: `source`, `provider`, `model`, `revision`, `license_id`, `license_lane`, `commercial_use`.
- Optional: `runpack_fingerprint`, `notes`.
- Production eligibility requires production lane, complete provenance, valid hashes, successful status, contract validation, and human approval.

## VideoJob

- `schema_version`, `id`, `workflow`, `inputs`, `models`, `references`, `resource_budget`, `expected_output`.
- Reference paths are represented as logical names and hashes; secrets, absolute paths and parent traversal are invalid.
- Fingerprint is SHA-256 of canonical sorted JSON excluding the fingerprint field.

