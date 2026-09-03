from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from ..config import load_drama_project
from ..errors import RuntimeExecutionError, ValidationError
from ..media import probe_media, run_ffmpeg
from ..paths import local_path
from ..policy.gates import sha256_file, verify_sha256
from ..schemas import DramaProject, DramaShot, LicenseLane, RunManifest, SourceArtifact
from ..tracking import load_manifest, save_manifest
from ..tracking.manifest import add_output, create_manifest
from ..tracking.mlflow_store import log_manifest
from ..workflows import WorkflowRunner
from .cache import cached_shot, record_cached_shot, shot_fingerprint


def _resolve_project_path(value: str | None, project_path: Path) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    return (path if path.is_absolute() else project_path.parent / path).resolve()


def _source_output(
    shot: DramaShot,
    *,
    parent_run_id: str,
    parent_mlflow_id: str | None,
) -> tuple[RunManifest, Path, str]:
    if shot.source_run_id:
        source_manifest = load_manifest(shot.source_run_id)
    else:
        payload = WorkflowRunner().run(
            str(shot.workflow),
            shot.inputs,
            parent_run_id=parent_run_id,
            parent_mlflow_id=parent_mlflow_id,
        )
        source_manifest = RunManifest.model_validate(payload)
    if source_manifest.status != "succeeded":
        raise ValidationError(f"Drama shot source did not succeed: {shot.id}")
    output = next(
        (
            item
            for item in source_manifest.outputs
            if item.media_kind == "video" or Path(item.path).suffix.lower() in {".mp4", ".mkv", ".mov", ".webm"}
        ),
        None,
    )
    if output is None:
        raise ValidationError(f"Drama shot source has no video output: {shot.id}")
    source_path = Path(output.path).expanduser().resolve()
    verify_sha256(source_path, output.sha256)
    return source_manifest, source_path, output.sha256


def _normalize_shot(source: Path, destination: Path, project: DramaProject) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    video_filter = (
        f"scale={project.width}:{project.height}:force_original_aspect_ratio=decrease,"
        f"pad={project.width}:{project.height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={project.fps:g},setsar=1,format=yuv420p"
    )
    return run_ffmpeg(
        [
            "-y",
            "-i",
            str(source),
            "-an",
            "-vf",
            video_filter,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(destination),
        ]
    )


def _assemble_shots(
    shots: list[tuple[DramaShot, Path]],
    destination: Path,
) -> dict[str, Any]:
    if len(shots) == 1:
        return run_ffmpeg(["-y", "-i", str(shots[0][1]), "-an", "-c:v", "copy", str(destination)])
    arguments: list[str] = ["-y"]
    durations: list[float] = []
    for _, path in shots:
        arguments.extend(["-i", str(path)])
        duration = probe_media(path).duration_seconds
        if not duration:
            raise RuntimeExecutionError(f"Normalized shot has no duration: {path.name}")
        durations.append(duration)
    if all(shot.transition == "cut" for shot, _ in shots[1:]):
        inputs = "".join(f"[{index}:v]" for index in range(len(shots)))
        graph = f"{inputs}concat=n={len(shots)}:v=1:a=0[vout]"
    else:
        filters: list[str] = []
        previous = "[0:v]"
        timeline = durations[0]
        for index, (shot, _) in enumerate(shots[1:], start=1):
            transition_duration = shot.fade_seconds if shot.transition == "fade" else 0.001
            if transition_duration >= min(timeline, durations[index]):
                raise ValidationError(
                    f"Transition is longer than adjacent shot duration: {shot.id}",
                    details={"duration": transition_duration},
                )
            offset = max(0.001, timeline - transition_duration)
            output = f"[v{index}]"
            filters.append(
                f"{previous}[{index}:v]xfade=transition=fade:duration={transition_duration:g}:offset={offset:g}{output}"
            )
            previous = output
            timeline += durations[index] - transition_duration
        graph = ";".join(filters) + f";{previous}format=yuv420p[vout]"
    arguments.extend(
        [
            "-filter_complex",
            graph,
            "-map",
            "[vout]",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(destination),
        ]
    )
    return run_ffmpeg(arguments)


def _subtitle_filter(path: Path) -> str:
    value = path.as_posix().replace("'", r"\'")
    if re.match(r"^[A-Za-z]:", value):
        value = f"{value[0]}\\:{value[2:]}"
    return f"subtitles=filename='{value}'"


def _mux_delivery(
    staged: Path,
    destination: Path,
    *,
    audio: Path | None,
    subtitles: Path | None,
    burn_subtitles: bool,
) -> dict[str, Any] | None:
    if audio is None and not burn_subtitles:
        shutil.copy2(staged, destination)
        return None
    arguments = ["-y", "-i", str(staged)]
    if audio is not None:
        arguments.extend(["-i", str(audio)])
    arguments.extend(["-map", "0:v:0"])
    if audio is not None:
        arguments.extend(["-map", "1:a:0", "-af", "apad", "-c:a", "aac", "-shortest"])
    if burn_subtitles:
        if subtitles is None:
            raise ValidationError("burn_subtitles requires subtitle_path")
        arguments.extend(["-vf", _subtitle_filter(subtitles), "-c:v", "libx264", "-pix_fmt", "yuv420p"])
    else:
        arguments.extend(["-c:v", "copy"])
    arguments.extend(["-movflags", "+faststart", str(destination)])
    return run_ffmpeg(arguments)


def _validate_delivery(
    path: Path,
    project: DramaProject,
    *,
    expected_duration: float,
) -> tuple[dict[str, bool], dict[str, Any]]:
    media = probe_media(path)
    validations = {
        "dimensions": media.width == project.width and media.height == project.height,
        "fps": media.fps is not None and abs(media.fps - project.fps) <= 0.01,
        "duration": bool(media.duration_seconds and media.duration_seconds > 0),
        "av_boundary": media.duration_seconds is not None
        and abs(media.duration_seconds - expected_duration) <= max(0.15, 2 / project.fps),
        "audio_stream": media.has_audio == bool(project.audio_path),
        "black_frames": True,
    }
    black = run_ffmpeg(["-i", str(path), "-vf", "blackdetect=d=0.5:pix_th=0.10", "-an", "-f", "null", os.devnull])
    durations = [float(value) for value in re.findall(r"black_duration:([0-9.]+)", black["stderr"])]
    if any(value >= 0.5 for value in durations):
        validations["black_frames"] = False
    if not all(validations.values()):
        raise ValidationError(
            "Drama delivery validation failed",
            details={"validations": validations, "black_durations": durations},
        )
    return validations, media.model_dump(mode="json")


def _append_source_artifact(
    manifest: RunManifest,
    *,
    path: Path,
    sha256: str,
    role: str,
    run_id: str | None = None,
) -> None:
    manifest.source_artifacts.append(SourceArtifact(run_id=run_id, role=role, path=str(path), sha256=sha256))


def render_drama(project_path: Path, *, resume: bool = False) -> dict[str, Any]:
    source_project_path = project_path.expanduser().resolve()
    project = load_drama_project(source_project_path)
    project_sha = sha256_file(source_project_path)
    audio = _resolve_project_path(project.audio_path, source_project_path)
    subtitles = _resolve_project_path(project.subtitle_path, source_project_path)
    for role, path in (("audio", audio), ("subtitles", subtitles)):
        if path is not None and not path.is_file():
            raise ValidationError(f"Drama {role} file does not exist: {path}")
    reference_paths: list[tuple[str, Path]] = []
    for character, value in project.character_references.items():
        resolved = _resolve_project_path(value, source_project_path)
        if resolved is not None:
            reference_paths.append((f"character_reference_{character}", resolved))
    for index, value in enumerate(project.style_references, start=1):
        resolved = _resolve_project_path(value, source_project_path)
        if resolved is not None:
            reference_paths.append((f"style_reference_{index}", resolved))
    for _, path in reference_paths:
        if not path.is_file():
            raise ValidationError(f"Drama reference file does not exist: {path}")
    manifest = create_manifest(
        kind="drama_render",
        parameters={
            "project_id": project.id,
            "width": project.width,
            "height": project.height,
            "fps": project.fps,
            "resume": resume,
            "resumed_shots": 0,
        },
        license_lanes=[],
        source={"project_path": str(source_project_path), "project_sha256": project_sha},
    )
    manifest.governance.validations.update({"contract": False, "hashes": False, "provenance": False, "media": False})
    _append_source_artifact(manifest, path=source_project_path, sha256=project_sha, role="drama_project")
    for role, path in reference_paths:
        _append_source_artifact(manifest, path=path, sha256=sha256_file(path), role=role)
    save_manifest(manifest)
    parent_mlflow_id = log_manifest(
        manifest.run_id,
        {"kind": manifest.kind, "project": project.id},
        {},
    )
    if parent_mlflow_id:
        manifest.tracking["mlflow_run_id"] = parent_mlflow_id
    try:
        normalized: list[tuple[DramaShot, Path]] = []
        child_run_ids: list[str] = []
        lanes: list[LicenseLane] = []
        for shot in project.shots:
            source_manifest, source_path, source_sha = _source_output(
                shot,
                parent_run_id=manifest.run_id,
                parent_mlflow_id=parent_mlflow_id,
            )
            lanes.extend(source_manifest.governance.license_lanes)
            _append_source_artifact(
                manifest,
                path=source_path,
                sha256=source_sha,
                role=f"shot_source_{shot.id}",
                run_id=source_manifest.run_id,
            )
            fingerprint = shot_fingerprint(shot, source_sha, project)
            cache_dir = local_path("cache", "drama", project.id, shot.id)
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached = cached_shot(cache_dir, fingerprint) if resume else None
            if cached is None:
                cached = cache_dir / f"{fingerprint}.mp4"
                ffmpeg_result = _normalize_shot(source_path, cached, project)
                record_cached_shot(cached, fingerprint)
                reused = False
            else:
                ffmpeg_result = None
                reused = True
                manifest.parameters["resumed_shots"] += 1
            shot_media = probe_media(cached)
            if (
                shot_media.width != project.width
                or shot_media.height != project.height
                or shot_media.fps is None
                or abs(shot_media.fps - project.fps) > 0.01
            ):
                raise ValidationError(f"Normalized shot does not match project media contract: {shot.id}")
            if shot.expected_duration_seconds is not None and (
                shot_media.duration_seconds is None
                or abs(shot_media.duration_seconds - shot.expected_duration_seconds) > max(0.15, 2 / project.fps)
            ):
                raise ValidationError(
                    f"Drama shot duration does not match its expectation: {shot.id}",
                    details={
                        "expected": shot.expected_duration_seconds,
                        "actual": shot_media.duration_seconds,
                    },
                )
            child = create_manifest(
                kind="drama_render",
                parameters={
                    "project_id": project.id,
                    "shot_id": shot.id,
                    "fingerprint": fingerprint,
                    "resumed": reused,
                    "ffmpeg": ffmpeg_result,
                },
                license_lanes=source_manifest.governance.license_lanes,
                source={"source_run_id": source_manifest.run_id, "source_sha256": source_sha},
                parent_run_id=manifest.run_id,
            )
            child.governance.validations.update({"contract": True, "hashes": True, "provenance": True, "media": True})
            _append_source_artifact(
                child,
                path=source_path,
                sha256=source_sha,
                role="source_video",
                run_id=source_manifest.run_id,
            )
            add_output(
                child,
                cached,
                role="normalized_shot",
                media_kind="video",
                media=shot_media,
                derived_from_run_id=source_manifest.run_id,
                derived_from_sha256=source_sha,
            )
            child.status = "succeeded"
            child_mlflow_id = log_manifest(
                child.run_id,
                {"kind": "drama_shot", "shot_id": shot.id, "resumed": reused},
                {"duration_seconds": float(shot_media.duration_seconds or 0)},
                artifacts=[cached],
                parent_run_id=parent_mlflow_id,
            )
            if child_mlflow_id:
                child.tracking["mlflow_run_id"] = child_mlflow_id
            save_manifest(child)
            child_run_ids.append(child.run_id)
            normalized.append((shot, cached))
        manifest.governance.license_lanes = list(dict.fromkeys(lanes))
        manifest.parameters["shot_run_ids"] = child_run_ids
        run_dir = local_path("runs", manifest.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        staged = run_dir / "visual-track.mp4"
        assembly = _assemble_shots(normalized, staged)
        staged_duration = probe_media(staged).duration_seconds
        if staged_duration is None:
            raise RuntimeExecutionError("Assembled visual track has no duration")
        output = run_dir / f"{project.id}.mp4"
        mux = _mux_delivery(
            staged,
            output,
            audio=audio,
            subtitles=subtitles,
            burn_subtitles=project.burn_subtitles,
        )
        validations, _ = _validate_delivery(output, project, expected_duration=staged_duration)
        output_media = probe_media(output)
        add_output(manifest, output, role="drama_video", media_kind="video", media=output_media)
        selected_artifacts = [output]
        if subtitles is not None:
            subtitle_output = run_dir / f"{project.id}.srt"
            shutil.copy2(subtitles, subtitle_output)
            add_output(manifest, subtitle_output, role="subtitles", media_kind="subtitle")
            selected_artifacts.append(subtitle_output)
            _append_source_artifact(
                manifest,
                path=subtitles,
                sha256=sha256_file(subtitles),
                role="external_subtitles",
            )
        if audio is not None:
            _append_source_artifact(
                manifest,
                path=audio,
                sha256=sha256_file(audio),
                role="external_audio",
            )
        for artifact in manifest.source_artifacts:
            verify_sha256(Path(str(artifact.path)), artifact.sha256)
        for item in manifest.outputs:
            verify_sha256(Path(item.path), item.sha256)
        manifest.parameters["ffmpeg_assembly"] = assembly
        manifest.parameters["ffmpeg_mux"] = mux
        manifest.governance.validations.update(
            {"contract": True, "hashes": True, "provenance": True, "media": all(validations.values())}
        )
        manifest.status = "succeeded"
        tracked = log_manifest(
            manifest.run_id,
            {"kind": manifest.kind, "project": project.id, "status": "succeeded"},
            {
                "shot_count": float(len(project.shots)),
                "resumed_shots": float(manifest.parameters["resumed_shots"]),
                "duration_seconds": float(output_media.duration_seconds or 0),
            },
            artifacts=selected_artifacts,
            existing_run_id=parent_mlflow_id,
        )
        if tracked:
            manifest.tracking["mlflow_run_id"] = tracked
    except Exception as exc:
        manifest.status = "failed"
        manifest.error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "details": getattr(exc, "details", {}),
        }
        save_manifest(manifest)
        raise
    save_manifest(manifest)
    return json.loads(manifest.model_dump_json())
