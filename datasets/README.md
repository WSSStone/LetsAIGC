# Datasets

Only descriptions, schemas and `.dvc` pointer files belong in Git. DVC materializes
owned bytes under `datasets/<id>/`; per-dataset `.gitignore` entries keep those bytes
out of Git while `.local/dvc-remote` stores the local remote copy. Scratch or
unversioned data stays under `.local/datasets`. Each dataset must document provenance,
consent/license, filtering, captioning method and intended model lane.
