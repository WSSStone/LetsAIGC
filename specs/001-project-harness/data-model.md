# Data Model

## ModelEntry

Stable ID; family/title; repository and immutable revision; files with source/local
paths, SHA-256 and sizes; safe format; license ID/URL/lane; gated flag; profiles;
VRAM/RAM/disk guidance; default-enabled flag. Installed bytes must match the catalog.

## WorkflowContract

ID/version; UI and API JSON paths; input/output JSON Schemas; model/node dependencies;
VRAM/RAM/disk/timeout budget; export lanes. Both workflow forms and every dependency
must exist, and inputs are validated before `/prompt` submission.

## RunManifest

Identity/timestamps/status; Git/Comfy/trainer revisions; workflow/config/model hashes;
parameters/seed/input hashes; hardware/software; outputs/timings/peak VRAM/errors;
MLflow/DVC references; license/validation/human gates.

State flow: `created -> validated -> queued -> running -> succeeded|failed|cancelled`.
Only succeeded runs with all gates may be promoted.

## AdapterRecord

Adapter/base model IDs and hashes; dataset path and DVC revision; training config/hash
and effective parameters; trainer revision; MLflow run; evaluation; license lane;
promotion status and approval. State flow: `candidate -> evaluated -> approved|rejected`.

## LicenseAttestation

Model/license IDs and URL; operator, timestamp, statement and source revision. It is
stored under `.local/state` and records acknowledgement, not legal eligibility.
