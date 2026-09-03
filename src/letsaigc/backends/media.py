from __future__ import annotations

from pathlib import Path

from ..config import load_typed
from ..drama import render_drama
from ..errors import ValidationError
from ..paths import find_repo_root
from ..policy import verify_sha256
from ..policy.gates import sha256_file
from ..schemas import DramaProject, GenerationIntent, GenerationPlan, RunManifest
from ..sprites import build_sprite_sequence
from ..tracking import load_manifest
from .base import GenerationResult


class MediaToolBackend:
    """Narrow adapters over existing deterministic media services."""

    name = "media"

    def execute(self, plan: GenerationPlan, *, iteration_id: str) -> GenerationResult:
        if plan.intent == GenerationIntent.sprite_sequence:
            source_run_id = str(plan.parameters.get("source_run_id", ""))
            profile = self._approved_config(
                str(plan.parameters.get("profile", "")),
                find_repo_root() / "configs" / "sprites",
            )
            if not source_run_id or not profile.is_file():
                raise ValidationError("Sprite Agent tool requires a source run and approved profile")
            payload = build_sprite_sequence(source_run_id=source_run_id, profile_path=profile)
        elif plan.intent == GenerationIntent.short_drama:
            project = self._approved_config(
                str(plan.parameters.get("project", "")),
                find_repo_root() / "configs" / "drama",
            )
            if not project.is_file():
                raise ValidationError("Drama Agent tool requires an approved project file")
            specification = load_typed(project, DramaProject)
            if any(shot.workflow for shot in specification.shots):
                raise ValidationError(
                    "Agent drama postprocessing accepts recorded shots only; "
                    "plan and approve shot generation separately"
                )
            payload = render_drama(project, resume=bool(plan.parameters.get("resume", True)))
        else:
            raise ValidationError("Media tool does not support this generation intent")
        manifest = RunManifest.model_validate(payload)
        if plan.intent == GenerationIntent.sprite_sequence:
            selected = [item for item in manifest.outputs if item.role == "sprite_sheet"]
        else:
            selected = [item for item in manifest.outputs if item.role == "drama_video"]
        if len(selected) != 1:
            raise ValidationError("Media Agent tool did not produce exactly one primary output")
        return GenerationResult(
            outputs=[Path(selected[0].path)],
            run_id=manifest.run_id,
        )

    @classmethod
    def dependencies(cls, plan: GenerationPlan) -> dict[str, str]:
        """Freeze configurations, input run records, media, audio and references before approval."""
        root = find_repo_root()
        if plan.intent == GenerationIntent.sprite_sequence:
            config = cls._approved_config(str(plan.parameters.get("profile", "")), root / "configs/sprites")
            source_ids = [str(plan.parameters.get("source_run_id", ""))]
            paths = [config]
        else:
            config = cls._approved_config(str(plan.parameters.get("project", "")), root / "configs/drama")
            project = load_typed(config, DramaProject)
            if any(shot.workflow for shot in project.shots):
                raise ValidationError("Approve each generated drama shot separately before postprocessing")
            source_ids = [str(shot.source_run_id) for shot in project.shots]
            values = [
                project.audio_path, project.subtitle_path,
                *project.character_references.values(), *project.style_references,
            ]
            paths = [config]
            for value in values:
                if value:
                    path = Path(value)
                    paths.append(path if path.is_absolute() else config.parent / path)
            plan.envelope.max_width = project.width
            plan.envelope.max_height = project.height
        for source_id in source_ids:
            allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            if not source_id or any(char not in allowed for char in source_id):
                raise ValidationError("Media source must be an existing run id")
            source = load_manifest(source_id)
            if source.status != "succeeded":
                raise ValidationError("Media source run must have succeeded")
            paths.append(root / ".local/runs" / source_id / "manifest.json")
            for output in source.outputs:
                verify_sha256(Path(output.path), output.sha256)
                paths.append(Path(output.path))
        frozen = {}
        for path in paths:
            resolved = path.resolve()
            if root.resolve() not in resolved.parents:
                raise ValidationError("Agent media references must be staged inside the project before approval")
            frozen[resolved.relative_to(root.resolve()).as_posix()] = sha256_file(resolved)
        return frozen

    @staticmethod
    def _approved_config(value: str, allowed_root: Path) -> Path:
        if not value:
            raise ValidationError("Media Agent tool configuration is missing")
        source = Path(value).expanduser()
        resolved = (source if source.is_absolute() else find_repo_root() / source).resolve()
        root = allowed_root.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValidationError("Media Agent tool configuration is outside its approved config root")
        return resolved
