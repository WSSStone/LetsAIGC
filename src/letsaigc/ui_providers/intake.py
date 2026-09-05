"""Trusted CLI intake. Runtime providers receive only frozen task-scoped references."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ..assets.http_safety import private_http
from ..assets.resolver import AssetResolver
from ..assets.store import ArtifactStore
from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, canonical_json
from ..schemas.ui import UIResourceLimits
from ..schemas.ui_provider import ManualUIInput, UIInputEntry, UIInputManifest, UISource
from ..ui_analysis.normalize import validate_ui_image


class UIIntake:
    def __init__(self, store: ArtifactStore, *, resolver: AssetResolver | None = None):
        self.store = store
        self.resolver = resolver or AssetResolver()

    def import_images(self, inputs, *, user_declared=None, allowed_scopes=frozenset()) -> ArtifactRef:
        if not 1 <= len(inputs) <= 10:
            raise PipelineError("input_count", "UI intake accepts 1 to 10 inputs before deduplication")
        metadata = user_declared or {}
        if not isinstance(metadata, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in metadata.items()
        ):
            raise PipelineError("invalid_metadata")
        if len(canonical_json(metadata).encode()) > 16 * 1024:
            raise PipelineError("invalid_metadata")
        task_id = "intake-" + uuid4().hex
        entries, sources, seen = [], [], {}
        for index, value in enumerate(inputs):
            entry_id = f"input-{index + 1}"
            try:
                data, media_type, origin, source_page, source_ids = self._read(value, allowed_scopes)
                _, width, height, media_type = validate_ui_image(data, media_type, UIResourceLimits())
                sha = hashlib.sha256(data).hexdigest()
                original = self.store.put(
                    task_id, "intake", data, role="original", media_type=media_type, source_ids=source_ids
                )
                provenance = {
                    "schema_version": 1,
                    "origin": origin,
                    "acquisition_method": "manual",
                    "acquired_at": datetime.now(UTC).isoformat(),
                    "source_page": source_page,
                    "observed": {"sha256": sha, "width": width, "height": height, "media_type": media_type},
                    "user_declared": metadata,
                    "provider_declared": {},
                    "license_status": "unknown",
                }
                provenance_ref = self.store.put(
                    task_id,
                    "intake",
                    canonical_json(provenance).encode(),
                    role="provenance",
                    source_ids=[original.artifact_id],
                )
                source_id = "source-" + sha[:24] + "-" + str(index + 1)
                sources.append(
                    UISource(
                        source_id=source_id,
                        original_ref=original,
                        provenance_ref=provenance_ref,
                        input_entry_ids=[entry_id],
                    )
                )
                entries.append(
                    UIInputEntry(entry_id=entry_id, status="ready", source_id=source_id, duplicate_of=seen.get(sha))
                )
                seen.setdefault(sha, entry_id)
            except Exception:
                entries.append(UIInputEntry(entry_id=entry_id, status="failed", error_code="invalid_input"))
        status = "unavailable" if not sources else "ready" if len(sources) == len(entries) else "partial"
        manifest = UIInputManifest(task_id=task_id, entries=entries, sources=sources, status=status)
        return self.store.put(task_id, "intake", canonical_json(manifest).encode(), role="input_manifest")

    def _read(self, value, allowed_scopes):
        if isinstance(value, ArtifactRef):
            if value.task_id not in allowed_scopes or value.role not in {"original", "canonical"}:
                raise PipelineError("artifact_scope")
            return self.store.read(value), value.media_type, "artifact", None, [value.artifact_id]
        if not isinstance(value, str):
            raise PipelineError("invalid_input")
        if value.lower().startswith("https://"):
            with private_http():
                data, media_type, page = self.resolver._fetch_https(value)
            return data, media_type, "https", page, []
        if "://" in value or value.lower().startswith(("http:", "file:")):
            raise PipelineError("invalid_input")
        path = Path(value).expanduser()
        if not path.is_file() or path.stat().st_size > 25 * 1024**2:
            raise PipelineError("invalid_input")
        with path.open("rb") as source:
            data = source.read(25 * 1024**2 + 1)
        media_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
            path.suffix.lower(), "application/octet-stream"
        )
        return data, media_type, "local", None, []

    def rebind(self, manifest_ref: ArtifactRef, task_id: str, *, allowed_scopes) -> ManualUIInput:
        if manifest_ref.task_id not in allowed_scopes or manifest_ref.role != "input_manifest":
            raise PipelineError("artifact_scope")
        manifest = UIInputManifest.model_validate_json(self.store.read(manifest_ref))
        if manifest.task_id != manifest_ref.task_id or not manifest.sources:
            raise PipelineError("invalid_input")
        sources = []
        for source in manifest.sources:
            refs = [
                self.store.put(
                    task_id,
                    "intake",
                    self.store.read(ref),
                    role=ref.role,
                    media_type=ref.media_type,
                    source_ids=[ref.artifact_id],
                )
                for ref in (source.original_ref, source.provenance_ref)
            ]
            sources.append(source.model_copy(update={"original_ref": refs[0], "provenance_ref": refs[1]}))
        manifest = UIInputManifest(task_id=task_id, status=manifest.status, entries=manifest.entries, sources=sources)
        ref = self.store.put(
            task_id,
            "intake",
            canonical_json(manifest).encode(),
            role="input_manifest",
            source_ids=[manifest_ref.artifact_id],
        )
        return ManualUIInput(inputs=[source.original_ref for source in sources], metadata_ref=ref)


def frozen_manifest(store, request: ManualUIInput, task_id: str):
    if request.metadata_ref:
        if request.metadata_ref.task_id != task_id or request.metadata_ref.role != "input_manifest":
            raise PipelineError("artifact_scope")
        manifest = UIInputManifest.model_validate_json(store.read(request.metadata_ref))
        if manifest.task_id != task_id or [source.original_ref for source in manifest.sources] != request.inputs:
            raise PipelineError("artifact_scope")
        for source in manifest.sources:
            store.read(source.provenance_ref)
        return manifest
    raise PipelineError("missing_provenance", "Manual inputs require their frozen intake manifest")
