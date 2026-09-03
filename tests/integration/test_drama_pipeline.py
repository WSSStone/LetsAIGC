from __future__ import annotations

from pathlib import Path

import yaml

from letsaigc.drama.pipeline import render_drama
from letsaigc.media import probe_media, run_ffmpeg
from letsaigc.policy.gates import sha256_file
from letsaigc.schemas import LicenseLane, RunGovernance, RunManifest, RunOutput
from letsaigc.tracking import load_manifest, save_manifest
from letsaigc.tracking.mlflow_store import tracking_uri


def _source_run(root: Path, run_id: str, color: str) -> RunManifest:
    video = root / f"{run_id}.mp4"
    run_ffmpeg(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=64x64:d=0.5:r=12",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ]
    )
    media = probe_media(video)
    manifest = RunManifest(
        run_id=run_id,
        kind="video_generation",
        status="succeeded",
        source={"fixture": True},
        parameters={},
        environment={},
        outputs=[
            RunOutput(
                path=str(video),
                sha256=sha256_file(video),
                size_bytes=video.stat().st_size,
                role="primary_video",
                media_kind="video",
                media=media,
            )
        ],
        governance=RunGovernance(
            license_lanes=[LicenseLane.production],
            validations={"contract": True, "hashes": True, "provenance": True},
        ),
    )
    save_manifest(manifest)
    return manifest


def test_two_shot_project_muxes_audio_subtitle_and_resumes(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n", encoding="utf-8")
    (root / "configs").mkdir()
    monkeypatch.setenv("LETSAIGC_ROOT", str(root))
    _source_run(root, "shot-red", "red")
    _source_run(root, "shot-blue", "blue")
    audio = root / "dialogue.wav"
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "pcm_s16le", str(audio)])
    subtitle = root / "dialogue.srt"
    subtitle.write_text("1\n00:00:00,000 --> 00:00:00,900\nHello\n", encoding="utf-8")
    project = {
        "schema_version": 1,
        "id": "fixture-drama",
        "width": 64,
        "height": 64,
        "fps": 12,
        "shots": [
            {"id": "red", "source_run_id": "shot-red", "expected_duration_seconds": 0.5},
            {
                "id": "blue",
                "source_run_id": "shot-blue",
                "transition": "fade",
                "fade_seconds": 0.1,
                "expected_duration_seconds": 0.5,
            },
        ],
        "audio_path": str(audio),
        "subtitle_path": str(subtitle),
        "burn_subtitles": False,
    }
    project_path = root / "project.yaml"
    project_path.write_text(yaml.safe_dump(project), encoding="utf-8")

    first = render_drama(project_path, resume=True)
    second = render_drama(project_path, resume=True)
    assert first["status"] == second["status"] == "succeeded"
    assert second["parameters"]["resumed_shots"] == 2
    assert any(item["role"] == "drama_video" for item in second["outputs"])
    assert any(item["role"] == "subtitles" for item in second["outputs"])
    video = next(item for item in second["outputs"] if item["role"] == "drama_video")
    assert video["media"]["has_audio"] is True
    children = [load_manifest(run_id) for run_id in second["parameters"]["shot_run_ids"]]
    assert all(child.parent_run_id == second["run_id"] for child in children)
    parent_mlflow_id = second["tracking"].get("mlflow_run_id")
    if parent_mlflow_id:
        from mlflow import MlflowClient

        client = MlflowClient(tracking_uri=tracking_uri())
        assert all(
            client.get_run(child.tracking["mlflow_run_id"]).data.tags["mlflow.parentRunId"]
            == parent_mlflow_id
            for child in children
        )
        experiment = client.get_experiment_by_name("letsaigc-local")
        assert experiment is not None
        assert ".local/mlflow/artifacts" in experiment.artifact_location.replace("\\", "/")

    project["burn_subtitles"] = True
    project_path.write_text(yaml.safe_dump(project), encoding="utf-8")
    burned = render_drama(project_path, resume=True)
    assert burned["status"] == "succeeded"
    assert burned["parameters"]["resumed_shots"] == 2
