from __future__ import annotations

from pathlib import Path

import yaml
from PIL import Image, ImageDraw

from letsaigc.config import load_license_policy
from letsaigc.media import run_ffmpeg
from letsaigc.policy.gates import evaluate_export
from letsaigc.schemas import LicenseLane, RunManifest
from letsaigc.sprites.pipeline import build_sprite_sequence, validate_sprite_run


def test_direct_video_build_is_tracked_but_development_only(tmp_path: Path, monkeypatch) -> None:
    license_policy = load_license_policy()
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n", encoding="utf-8")
    (root / "configs").mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    for index, x in enumerate((8, 12, 16), start=1):
        image = Image.new("RGB", (64, 64), "#00FF00")
        ImageDraw.Draw(image).rectangle((x, 20, x + 15, 55), fill="#E02020")
        image.save(raw / f"{index:06d}.png")
    video = tmp_path / "source.mp4"
    run_ffmpeg(
        ["-y", "-framerate", "12", "-i", str(raw / "%06d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)]
    )
    profile = yaml.safe_load((Path(__file__).parents[2] / "configs/sprites/general-rgba-512.yaml").read_text())
    profile.update({"canvas_width": 64, "canvas_height": 64, "max_frames": 8})
    profile["atlas"].update({"max_width": 512, "max_height": 512})
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    monkeypatch.setenv("LETSAIGC_ROOT", str(root))
    monkeypatch.setattr("letsaigc.sprites.pipeline.log_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr("letsaigc.policy.gates.load_license_policy", lambda: license_policy)

    result = build_sprite_sequence(input_path=video, profile_path=profile_path)
    assert result["kind"] == "sprite_pipeline"
    assert result["status"] == "succeeded"
    assert result["governance"]["validations"]["provenance"] is False
    assert any(item["role"] == "sprite_sheet" for item in result["outputs"])
    assert validate_sprite_run(result["run_id"])["valid"] is True
    decision = evaluate_export(RunManifest.model_validate(result), lane=LicenseLane.production)
    assert decision.allowed is False
    assert "validation gates failed" in " ".join(decision.reasons)
