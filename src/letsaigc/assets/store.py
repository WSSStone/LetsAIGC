"""Immutable, task-scoped local artifact storage; payloads carry relative references."""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from pydantic import TypeAdapter

from ..files import replace_file
from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, Identifier


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve(self, ref: ArtifactRef) -> Path:
        ref = ArtifactRef.model_validate(ref.model_dump())
        path = (self.root / ref.key).resolve()
        if not path.is_relative_to(self.root):
            raise PipelineError("unsafe_artifact", "Artifact escapes the configured store")
        # Deep worktrees and content-addressed keys routinely exceed MAX_PATH.
        if os.name == "nt" and not str(path).startswith("\\\\?\\"):
            return Path("\\\\?\\" + str(path))
        return path

    def read(self, ref: ArtifactRef) -> bytes:
        path = self.resolve(ref)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise PipelineError("missing_artifact", "Referenced artifact is unavailable") from exc
        if len(data) != ref.size_bytes or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise PipelineError("artifact_changed", "Referenced artifact failed content verification")
        return data

    def put(
        self,
        task_id: str,
        operation_id: str,
        data: bytes,
        *,
        role: str,
        media_type: str = "application/json",
        source_ids: list[str] | None = None,
    ) -> ArtifactRef:
        for value in (task_id, operation_id, role):
            TypeAdapter(Identifier).validate_python(value)
        sha256 = hashlib.sha256(data).hexdigest()
        key = f"{task_id}/{operation_id}/{role}/{sha256}"
        ref = ArtifactRef(
            task_id=task_id,
            artifact_id=f"asset-{hashlib.sha256((sha256 + role).encode()).hexdigest()[:48]}",
            key=key,
            sha256=sha256,
            size_bytes=len(data),
            role=role,
            media_type=media_type,
            operation_id=operation_id,
            source_ids=source_ids or [],
        )
        target = self.resolve(ref)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self.read(ref)
            return ref
        temporary = target.with_name(f".{sha256}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            # Same digest means concurrent publishers write the same verified content.
            replace_file(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return ref
