from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from letsaigc.drama.cache import shot_fingerprint
from letsaigc.schemas import DramaProject


def _project() -> dict:
    return {
        "schema_version": 1,
        "id": "episode-01",
        "width": 1280,
        "height": 720,
        "fps": 24,
        "shots": [
            {"id": "shot-01", "source_run_id": "video-1"},
            {"id": "shot-02", "workflow": "wan21-t2v-smoke", "inputs": {"seed": 9}, "transition": "fade"},
        ],
    }


def test_drama_project_requires_unique_shots_and_one_source() -> None:
    assert len(DramaProject.model_validate(_project()).shots) == 2
    invalid = _project()
    invalid["shots"][0]["workflow"] = "also-set"
    with pytest.raises(PydanticValidationError, match="exactly one"):
        DramaProject.model_validate(invalid)
    duplicate = _project()
    duplicate["shots"][1]["id"] = "shot-01"
    with pytest.raises(PydanticValidationError, match="unique"):
        DramaProject.model_validate(duplicate)


def test_shot_fingerprint_is_stable_and_source_sensitive() -> None:
    project = DramaProject.model_validate(_project())
    shot = project.shots[0]
    first = shot_fingerprint(shot, "a" * 64, project)
    assert first == shot_fingerprint(shot, "a" * 64, project)
    assert first != shot_fingerprint(shot, "b" * 64, project)
