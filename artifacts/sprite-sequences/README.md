# Promoted sprite sequences

Only reviewed sprite sheets, RGBA frames and metadata belong here, tracked through DVC rather
than committed as binary Git blobs. A promotion must retain the source video run ID, source
SHA-256, sprite profile, validation evidence and human approval in its RunManifest.

Typical promotion flow:

```powershell
dvc add artifacts\sprite-sequences\ASSET_ID
dvc push
git add artifacts\sprite-sequences\ASSET_ID.dvc artifacts\sprite-sequences\.gitignore
```

Do not promote raw video, extraction scratch frames or an asset produced through direct
`sprites build --input`; those remain development-only under `.local/`.
