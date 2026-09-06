from __future__ import annotations

from pathlib import Path

import pytest

from letsaigc.errors import ValidationError
from letsaigc.media.artifacts import discover_declared_outputs
from letsaigc.media.tools import parse_fraction
from letsaigc.schemas import MediaOutputDeclaration


def test_parse_fraction_normalizes_ffprobe_values() -> None:
    assert parse_fraction("24000/1001") == pytest.approx(23.976, rel=1e-4)
    assert parse_fraction("12") == 12.0
    assert parse_fraction("0/0") is None
    assert parse_fraction("N/A") is None


@pytest.mark.parametrize("subfolder", ["clips/nested", r"clips\nested"])
def test_declared_output_discovery_is_role_and_extension_scoped(tmp_path: Path, subfolder: str) -> None:
    output = tmp_path / "clips" / "nested" / "sample.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"video")
    declaration = MediaOutputDeclaration(
        node_id="9",
        history_field="videos",
        role="primary_video",
        media_kind="video",
        allowed_extensions=[".mp4"],
    )
    history = {
        "outputs": {
            "9": {"videos": [{"filename": "sample.mp4", "subfolder": subfolder, "type": "output"}]},
            "10": {"videos": [{"filename": "untracked.mp4", "type": "output"}]},
        }
    }
    results = discover_declared_outputs(history, [declaration], tmp_path)
    assert [(item.path, item.role) for item in results] == [(output.resolve(), "primary_video")]


@pytest.mark.parametrize(
    ("subfolder", "filename"),
    [
        ("", "../escape.mp4"),
        ("", r"..\escape.mp4"),
        (r"clips\..", "escape.mp4"),
        ("../clips", "escape.mp4"),
        ("", "/escape.mp4"),
        ("", r"C:\escape.mp4"),
        ("", r"C:escape.mp4"),
        ("", r"\\server\share\escape.mp4"),
        (r"C:\clips", "escape.mp4"),
    ],
)
def test_declared_output_rejects_path_traversal(tmp_path: Path, subfolder: str, filename: str) -> None:
    declaration = MediaOutputDeclaration(
        node_id="9",
        history_field="videos",
        role="primary_video",
        media_kind="video",
        allowed_extensions=[".mp4"],
    )
    history = {"outputs": {"9": {"videos": [{"filename": filename, "subfolder": subfolder}]}}}
    with pytest.raises(ValidationError, match="safe relative path"):
        discover_declared_outputs(history, [declaration], tmp_path)
