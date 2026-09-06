"""T019 structure and CLI contracts; implementation gates belong to T020/T021."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError
from typer.main import get_command
from typer.testing import CliRunner

from letsaigc.cli import app
from letsaigc.schemas import ui

ROOT = Path(__file__).resolve().parents[2]


def selection():
    return json.loads((ROOT / "tests/fixtures/contracts/ui-edit-contracts.json").read_text())["selection"]


def validator():
    schema = json.loads((ROOT / "specs/013-game-ui-analysis/contracts/ui-selection.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_candidate_requires_stable_number_preview_evidence_and_has_no_approval_authority():
    schema = validator().schema
    candidate_validator = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/UISelectionCandidate"})
    ref = json.loads((ROOT / "tests/fixtures/contracts/ui-edit-contracts.json").read_text())[
        "masked_plan"
    ]["image_mask"]["selection_ref"]
    candidate = {
        "candidate_id": "candidate-1", "task_id": ref["task_id"], "selection_ref": ref,
        "preview_ref": {**ref, "role": "selection_preview"},
        "evidence_ref": {**ref, "role": "selection_evidence"},
        "origin": "agent_proposed", "revision": 0, "state": "proposed",
    }
    candidate_validator.validate(candidate)
    for field in ("candidate_id", "selection_ref", "preview_ref", "evidence_ref"):
        invalid = {key: value for key, value in candidate.items() if key != field}
        with pytest.raises(ValidationError):
            candidate_validator.validate(invalid)
    for change in ({"state": "approved"}, {"origin": "agent_approved"}, {"approval": True}):
        with pytest.raises(ValidationError):
            candidate_validator.validate({**candidate, **change})


def test_bbox_selection_accepts_one_and_ten_sources():
    value = selection()
    validator().validate(value)
    value["sources"] = [{**value["sources"][0], "source_id": f"source-{i}"} for i in range(10)]
    validator().validate(value)
    value["sources"].append({**value["sources"][0], "source_id": "source-11"})
    with pytest.raises(ValidationError):
        validator().validate(value)


@pytest.mark.parametrize("change", [
    {"target_regions": []},
    {"target_regions": [{"kind": "bbox", "xyxy": [0, 0, 1]}]},
    {"target_regions": [{"kind": "bbox", "xyxy": [-1, 0, 10, 10]}]},
    {"target_regions": [{"kind": "bbox", "xyxy": [False, 0, 10, 10]}]},
    {"target_regions": [{"kind": "polygon", "xyxy": [0, 0, 10, 10]}]},
    {"target_regions": [{"kind": "element", "element_id": "icon"}]},
    {"keep_elements": ["title"]},
    {"remove_elements": ["title"]},
    {"keep_elements": ["title", "title"]},
    {"original_sha256": "unverified"},
    {"metadata": {"target": "all"}},
])
def test_selection_rejects_ambiguous_or_unbounded_structure(change):
    value = selection()
    value["sources"][0].update(change)
    with pytest.raises(ValidationError):
        validator().validate(value)


def test_region_limit_and_duplicate_are_rejected():
    value = selection()
    regions = [{"kind": "bbox", "xyxy": [i, 0, i + 1, 1]} for i in range(64)]
    value["sources"][0]["target_regions"] = regions
    validator().validate(value)
    for extra in (regions[0], {"kind": "bbox", "xyxy": [64, 0, 65, 1]}):
        changed = copy.deepcopy(value)
        changed["sources"][0]["target_regions"].append(extra)
        with pytest.raises(ValidationError):
            validator().validate(changed)
    value["sources"][0]["target_regions"] = [regions[0], regions[0]]
    with pytest.raises(ValidationError):
        validator().validate(value)


def test_t020_selection_dto_matches_machine_contract():
    if not hasattr(ui, "UISelection"):
        pytest.xfail("T020: UISelection DTO not implemented; machine schema is not runtime validation")
    value = ui.UISelection.model_validate_json(json.dumps(selection()))
    validator().validate(value.model_dump(mode="json"))
    for xyxy in ([10, 0, 1, 1], [0, 0, 0, 1]):
        invalid = selection()
        invalid["sources"][0]["target_regions"][0]["xyxy"] = xyxy
        with pytest.raises(ValueError):
            ui.UISelection.model_validate_json(json.dumps(invalid))
    invalid = selection()
    invalid["sources"].append({**invalid["sources"][0], "original_sha256": "b" * 64})
    with pytest.raises(ValueError):
        ui.UISelection.model_validate_json(json.dumps(invalid))


@pytest.mark.parametrize("options", [[], ["--candidate", "candidate-1", "--selection", "selection.json"]])
def test_t021_select_requires_exactly_one_choice_before_loading_task(options):
    if "select" not in get_command(app).commands["ui"].commands:
        pytest.xfail("T021: ui select not registered")
    result = CliRunner().invoke(app, ["--json", "ui", "select", "missing-task", *options])
    assert result.exit_code == 2, result.stdout
    assert json.loads(result.stdout)["error"]["code"] == "invalid_selection"


def test_t021_select_on_parse_is_rejected_without_calls(tmp_path, monkeypatch):
    from letsaigc.pipelines.service import PipelineService
    from letsaigc.schemas.ui import UIAnalysisRequest
    from letsaigc.ui_analysis import cli

    if "select" not in get_command(app).commands["ui"].commands:
        pytest.xfail("T021: ui select not registered")
    service = PipelineService(tmp_path, ui_schema=4)
    ref = service.artifacts.put("parse", "input", b"original", role="original")
    service.ui_plan("parse", UIAnalysisRequest(input={"kind": "manual", "inputs": [ref]}, budget={
        "max_total_cost_usd": 1, "max_iteration_cost_usd": 1,
        "max_total_gpu_minutes": 0, "max_iteration_gpu_minutes": 0, "max_revisions": 2,
    }))
    monkeypatch.setattr(cli, "service", lambda: service)
    monkeypatch.setattr(service, "submit_step", lambda *a, **kw: pytest.fail("select invoked a provider"))
    result = CliRunner().invoke(app, ["--json", "ui", "select", "parse", "--candidate", "candidate-1"])
    assert result.exit_code == 2, result.stdout
    assert json.loads(result.stdout)["error"]["code"] == "invalid_selection"
    assert service.ledger.list_operations("parse") == []


def test_confirmed_review_and_automatic_layout_remain_distinct_frozen_selection_inputs(tmp_path):
    from io import BytesIO

    from PIL import Image

    from letsaigc.pipelines.errors import PipelineError
    from letsaigc.pipelines.service import PipelineService
    from letsaigc.schemas.pipeline import digest
    from letsaigc.schemas.ui_review import ReviewConfirm, ReviewDocument, ReviewPatch
    from letsaigc.ui_analysis.review import ReviewRepository

    service = PipelineService(tmp_path, ui_schema=4)
    root = service.smoke_plan("review-source")
    repo = ReviewRepository(service.ledger, service.artifacts)
    stream = BytesIO()
    Image.new("RGB", (64, 48)).save(stream, format="PNG")
    canonical = service.artifacts.put(root.task_id, "input", stream.getvalue(), role="canonical")
    automatic = service.artifacts.put(root.task_id, "input", b'{"schema_version":1}', role="layout")
    repo.initialize(ReviewDocument(task_id=root.task_id, source_id="source-1", width=64, height=48),
                    {"canonical_ref": canonical.model_dump(mode="json")})
    repo.confirm(root.task_id, ReviewConfirm(request_id="confirm-0", draft_revision=0))
    frozen = repo.binding(root.task_id, 0)
    repo.save(root.task_id, ReviewPatch(request_id="draft-1", base_confirmed_revision=0, actions=[]))
    with pytest.raises(PipelineError):
        repo.binding(root.task_id, 1)
    repo.confirm(root.task_id, ReviewConfirm(request_id="confirm-1", draft_revision=1, expected_confirmed_revision=0))
    newer = repo.binding(root.task_id, 1)
    hashes = []
    for layout in (automatic, frozen.layout_ref, newer.layout_ref):
        value = selection()
        value["sources"][0]["layout_ref"] = layout.model_dump(mode="json")
        validator().validate(value)
        hashes.append(digest(value))
    assert len(set(hashes)) == 3
    assert repo.binding(root.task_id, 0) == frozen
    assert service.ledger.plan(root.task_id).fingerprint == root.fingerprint
    assert service.ledger.list_operations(root.task_id) == []
