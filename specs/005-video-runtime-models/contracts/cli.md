# CLI Contract: Video Runtime and Models

All commands honor root `--json`; domain errors use the existing structured error envelope and non-zero exit codes.

## `video run WORKFLOW_ID [--set KEY=VALUE]...`

- Requires a `media_kind: video` workflow contract.
- Returns the workflow result plus `run_id`, manifest path, declared media outputs, and media probes.

## `video import PATH --metadata FILE`

- Copies the file to the run directory before hashing and probing.
- Metadata must validate as `VideoImportMetadata`.
- Returns a successful `video_generation` run or a persisted failed run.

## `runpack build --job FILE`

- Mutually exclusive with the legacy positional run-id form.
- Returns runpack path and request fingerprint.

## `runpack ingest RUNPACK --result PATH --metadata FILE`

- Verifies request fingerprint, metadata and result media.
- Returns the new tracked video run and its source linkage.

## Compatibility

- `workflow run`, `runpack build RUN_ID`, image manifests and existing exports remain valid.
- New output declarations are optional only for legacy image contracts.

