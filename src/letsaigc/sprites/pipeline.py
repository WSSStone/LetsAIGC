from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops

from ..config import load_sprite_profile
from ..errors import RuntimeExecutionError, ValidationError
from ..media import probe_media, run_ffmpeg
from ..paths import local_path
from ..policy.gates import sha256_file, verify_sha256
from ..schemas import LicenseLane, MediaMetadata, SourceArtifact, SpriteProfile, SpriteSheetMetadata
from ..tracking import load_manifest, save_manifest
from ..tracking.manifest import add_output, create_manifest
from ..tracking.mlflow_store import log_manifest
from .atlas import pack_sprite_sheet
from .processor import apply_chroma_key, normalize_frames
from .validation import validate_normalized_frames


def _source_video(
    source_run_id: str | None,
    input_path: Path | None,
) -> tuple[Path, str, list[LicenseLane], bool, str | None, str | None]:
    if bool(source_run_id) == bool(input_path):
        raise ValidationError("Specify exactly one of source_run_id or input_path")
    if source_run_id:
        source_manifest = load_manifest(source_run_id)
        if source_manifest.status != "succeeded":
            raise ValidationError(f"Source video run is not succeeded: {source_run_id}")
        output = next(
            (
                item
                for item in source_manifest.outputs
                if item.media_kind == "video" or Path(item.path).suffix.lower() in {".mp4", ".mkv", ".webm"}
            ),
            None,
        )
        if output is None:
            raise ValidationError(f"Source run has no video output: {source_run_id}")
        source = Path(output.path)
        verify_sha256(source, output.sha256)
        return (
            source,
            output.sha256,
            source_manifest.governance.license_lanes,
            True,
            source_run_id,
            source_manifest.tracking.get("mlflow_run_id"),
        )
    source = Path(input_path).expanduser().resolve()  # type: ignore[arg-type]
    if not source.is_file():
        raise ValidationError(f"Sprite source video does not exist: {source}")
    return source, sha256_file(source), [], False, None, None


def build_sprite_sequence(
    *,
    profile_path: Path,
    source_run_id: str | None = None,
    input_path: Path | None = None,
) -> dict:
    profile = load_sprite_profile(profile_path)
    source, source_sha, lanes, provenance, parent_run_id, parent_mlflow_id = _source_video(source_run_id, input_path)
    source_media = probe_media(source)
    manifest = create_manifest(
        kind="sprite_pipeline",
        parameters={"profile": profile.model_dump(mode="json")},
        license_lanes=lanes,
        source={"source_video": str(source), "source_video_sha256": source_sha},
        parent_run_id=parent_run_id,
    )
    manifest.source_artifacts.append(
        SourceArtifact(run_id=parent_run_id, role="source_video", path=str(source), sha256=source_sha)
    )
    manifest.governance.validations.update(
        {
            "contract": False,
            "hashes": False,
            "provenance": provenance,
            "transparency": False,
            "anchor": False,
            "atlas": False,
        }
    )
    save_manifest(manifest)
    try:
        run_dir = local_path("runs", manifest.run_id)
        raw_dir = run_dir / "frames" / "raw"
        frame_dir = run_dir / "frames" / "rgba"
        raw_dir.mkdir(parents=True, exist_ok=True)
        frame_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg_result = run_ffmpeg(
            [
                "-y",
                "-i",
                str(source),
                "-vf",
                f"fps={profile.fps:g}",
                "-frames:v",
                str(profile.max_frames),
                str(raw_dir / "%06d.png"),
            ],
            timeout_seconds=max(60, round((source_media.duration_seconds or 1) * 30)),
        )
        manifest.parameters["ffmpeg_extract"] = {
            "arguments": ffmpeg_result["arguments"],
            "stderr": ffmpeg_result["stderr"],
        }
        manifest.environment["media_tools"] = ffmpeg_result["tool"]
        raw_paths = sorted(raw_dir.glob("*.png"))
        if not raw_paths:
            raise RuntimeExecutionError("FFmpeg extraction produced no frames")
        keyed = [apply_chroma_key(Image.open(path), profile.background) for path in raw_paths]
        normalized = normalize_frames(keyed, profile)
        diagnostics = validate_normalized_frames(normalized, profile)
        if not diagnostics.valid:
            raise ValidationError(
                "Sprite frame validation failed",
                details={"failures": diagnostics.failures, "warnings": diagnostics.warnings},
            )
        frame_paths: list[Path] = []
        for index, frame in enumerate(normalized, start=1):
            path = frame_dir / f"{index:06d}.png"
            frame.image.save(path)
            frame_paths.append(path)
        sheet_path = run_dir / "sprite-sheet.png"
        atlas = pack_sprite_sheet(
            normalized,
            frame_paths,
            profile,
            sheet_path,
            source_sha,
            source_run_id=parent_run_id,
            warnings=diagnostics.warnings,
            validations={"frames": True, "transparency": True, "anchor": True, "atlas": True},
        )
        metadata_path = run_dir / "sprite-sheet.json"
        metadata_path.write_text(atlas.metadata.model_dump_json(indent=2), encoding="utf-8")
        for index, frame_path in enumerate(frame_paths, start=1):
            add_output(
                manifest,
                frame_path,
                role=f"frame_{index:06d}",
                media_kind="image",
                media=MediaMetadata(
                    mime_type="image/png",
                    width=profile.canvas_width,
                    height=profile.canvas_height,
                    has_alpha=True,
                ),
                derived_from_run_id=parent_run_id,
                derived_from_sha256=source_sha,
            )
        add_output(
            manifest,
            sheet_path,
            role="sprite_sheet",
            media_kind="image",
            media=MediaMetadata(
                mime_type="image/png",
                width=atlas.metadata.sheet_width,
                height=atlas.metadata.sheet_height,
                has_alpha=True,
            ),
            derived_from_run_id=parent_run_id,
            derived_from_sha256=source_sha,
        )
        add_output(
            manifest,
            metadata_path,
            role="sprite_metadata",
            media_kind="metadata",
            derived_from_run_id=parent_run_id,
            derived_from_sha256=source_sha,
        )
        verify_sha256(source, source_sha)
        for output in manifest.outputs:
            verify_sha256(Path(output.path), output.sha256)
        manifest.governance.validations.update(
            {"contract": True, "hashes": True, "transparency": True, "anchor": True, "atlas": True}
        )
        manifest.status = "succeeded"
        mlflow_run_id = log_manifest(
            manifest.run_id,
            {"kind": manifest.kind, "profile": profile.id, "source_run_id": parent_run_id or "direct"},
            {"frame_count": float(len(frame_paths)), "warning_count": float(len(diagnostics.warnings))},
            artifacts=[sheet_path, metadata_path],
            parent_run_id=parent_mlflow_id,
        )
        if mlflow_run_id:
            manifest.tracking["mlflow_run_id"] = mlflow_run_id
    except Exception as exc:
        manifest.status = "failed"
        manifest.error = {"type": type(exc).__name__, "message": str(exc), "details": getattr(exc, "details", {})}
        save_manifest(manifest)
        raise
    save_manifest(manifest)
    return json.loads(manifest.model_dump_json())


def validate_sprite_run(run_id: str) -> dict:
    manifest = load_manifest(run_id)
    if manifest.kind != "sprite_pipeline":
        raise ValidationError(f"Run is not a sprite pipeline: {run_id}")
    metadata_output = next((item for item in manifest.outputs if item.role == "sprite_metadata"), None)
    sheet_output = next((item for item in manifest.outputs if item.role == "sprite_sheet"), None)
    if metadata_output is None or sheet_output is None:
        raise ValidationError("Sprite run is missing sheet or metadata output")
    for output in manifest.outputs:
        verify_sha256(Path(output.path), output.sha256)
    metadata = SpriteSheetMetadata.model_validate_json(Path(metadata_output.path).read_text(encoding="utf-8"))
    with Image.open(sheet_output.path) as sheet:
        if sheet.mode != "RGBA" or sheet.size != (metadata.sheet_width, metadata.sheet_height):
            raise ValidationError("Sprite sheet dimensions or color mode do not match metadata")
    names = {Path(item.path).name for item in manifest.outputs}
    missing = [frame.filename for frame in metadata.frames if frame.filename not in names]
    if missing:
        raise ValidationError("Sprite metadata references missing frames", details={"missing": missing})
    profile = SpriteProfile.model_validate(manifest.parameters.get("profile"))
    raw_paths = sorted((Path(metadata_output.path).parent / "frames" / "raw").glob("*.png"))
    if not raw_paths:
        manifest.governance.validations["frames"] = False
        save_manifest(manifest)
        raise ValidationError("Sprite run has no retained raw frames for deterministic validation")
    keyed = [apply_chroma_key(Image.open(path), profile.background) for path in raw_paths]
    normalized = normalize_frames(keyed, profile)
    diagnostics = validate_normalized_frames(normalized, profile)
    if not diagnostics.valid:
        manifest.governance.validations["frames"] = False
        manifest.governance.human_approved = False
        save_manifest(manifest)
        raise ValidationError(
            "Sprite run fails current frame validation",
            details={"failures": diagnostics.failures, "warnings": diagnostics.warnings},
        )
    frame_outputs = sorted(
        (item for item in manifest.outputs if item.role and item.role.startswith("frame_")),
        key=lambda item: str(item.role),
    )
    if len(frame_outputs) != len(normalized):
        manifest.governance.validations["frames"] = False
        save_manifest(manifest)
        raise ValidationError("Sprite output frame count differs from deterministic reprocessing")
    for generated, output in zip(normalized, frame_outputs, strict=True):
        with Image.open(output.path) as existing:
            if ImageChops.difference(generated.image, existing.convert("RGBA")).getbbox() is not None:
                manifest.governance.validations["frames"] = False
                save_manifest(manifest)
                raise ValidationError(
                    f"Sprite output differs from deterministic reprocessing: {output.role}"
                )
    manifest.governance.validations["frames"] = True
    manifest.governance.validations["hashes"] = True
    save_manifest(manifest)
    return {
        "run_id": run_id,
        "valid": True,
        "frame_count": len(metadata.frames),
        "warnings": metadata.warnings,
        "production_eligible": all(manifest.governance.validations.values()),
    }
