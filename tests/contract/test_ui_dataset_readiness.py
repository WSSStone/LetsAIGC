"""Contract checks for the observed T035 dataset subset and lane isolation."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "ui_analysis"


def load_dataset() -> tuple[dict, dict]:
    cases = yaml.safe_load((FIXTURE_DIR / "cases.yaml").read_text(encoding="utf-8"))
    annotations = json.loads((FIXTURE_DIR / "annotations.json").read_text(encoding="utf-8"))
    return cases, annotations


def test_readiness_records_observed_subset_without_slot_fillers() -> None:
    cases, annotations = load_dataset()
    rows = cases["cases"]
    target = cases["target"]
    observed = cases["observed"]
    development_count = sum(row["split"] == "development" for row in rows)
    evaluation_count = sum(row["split"] == "evaluation" for row in rows)
    known_background_count = sum(row.get("known_background") is True for row in rows)
    ground_truth_count = sum(
        (annotations["cases"][row["case_id"]].get("independent_ground_truth") or {}).get(
            "ground_truth_eligible"
        )
        is True
        for row in rows
    )
    assert observed["total_cases"] == len(rows)
    assert observed["development_cases"] == development_count
    assert observed["evaluation_cases"] == evaluation_count
    assert observed["known_background_cases"] == known_background_count
    assert observed["missing_case_count"] == max(target["total_cases"] - len(rows), 0)
    assert observed["missing_evaluation_case_count"] == max(target["evaluation_cases"] - evaluation_count, 0)
    assert observed["missing_known_background_case_count"] == max(
        target["known_background_cases"] - known_background_count, 0
    )
    assert observed["missing_independent_ground_truth_case_count"] == max(
        target["total_cases"] - ground_truth_count, 0
    )
    complete = (
        len(rows) >= target["total_cases"]
        and evaluation_count >= target["evaluation_cases"]
        and known_background_count >= target["known_background_cases"]
        and ground_truth_count >= target["total_cases"]
    )
    assert (cases["dataset_status"] == "ready") is complete
    assert set(annotations["cases"]) == {row["case_id"] for row in rows}
    assert all("slot" not in row["case_id"] for row in rows)
    preview_ids = {"hud-en-landscape", "hud-zh-dense", "hud-portrait"}
    preview_rows = {row["case_id"]: row for row in rows if row["dataset_role"] == "synthetic_preview_fixture"}
    assert set(preview_rows) == preview_ids
    assert all(preview_rows[case_id]["split"] == "development" for case_id in preview_ids)


def test_git_fixture_files_are_hash_verified_and_local_sources_are_declared() -> None:
    cases, _ = load_dataset()
    for case in cases["cases"]:
        path = (ROOT / case["path"]).resolve()
        assert path.is_relative_to(ROOT.resolve())
        if case["distribution"] == "git_fixture":
            assert path.is_file(), case["path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == case["sha256"]
        else:
            assert case["source_status"] == "local_only_observed"
            assert case["distribution"] == "local_only"
            assert case["license_id"] == "unverified_reference_only"
        assert case["evaluation_eligible"] is False
    commercial_ids = {"hades2-en-landscape", "starrail-zh-dense", "clashroyale-en-portrait"}
    commercial = {row["case_id"]: row for row in cases["cases"] if row["dataset_role"] == "commercial_preview"}
    assert commercial_ids <= set(commercial)
    assert all(commercial[case_id]["split"] == "development" for case_id in commercial_ids)


def test_automatic_human_and_ground_truth_lanes_cannot_overlap() -> None:
    cases, annotations = load_dataset()
    for lane in ("automatic", "human_assisted", "independent_ground_truth"):
        if lane == "independent_ground_truth":
            lane_cases = {
                case_id
                for case_id, row in annotations["cases"].items()
                if (row.get(lane) or {}).get("ground_truth_eligible") is True
            }
        else:
            lane_cases = {case_id for case_id, row in annotations["cases"].items() if row.get(lane) is not None}
        assert set(annotations["lanes"][lane]["cases"]) == lane_cases
        assert annotations["lanes"][lane]["case_count"] == len(lane_cases)
    for case in cases["cases"]:
        row = annotations["cases"][case["case_id"]]
        if case["dataset_role"] == "synthetic_preview_fixture":
            assert row["source_annotation"]["ground_truth_eligible"] is False
            assert row["automatic"] is None and row["human_assisted"] is None
        else:
            automatic = row["automatic"]
            human = row["human_assisted"]
            ground_truth = row["independent_ground_truth"]
            assert automatic["evaluation_lane"] == "automatic"
            assert automatic["is_model_output"] is True
            assert automatic["ground_truth_eligible"] is False
            assert human["evaluation_lane"] == "human_assisted"
            assert human["is_review_head"] is True
            assert human["ground_truth_eligible"] is False
            assert ground_truth["status"] == "missing"
            assert ground_truth["independent"] is False
            assert ground_truth["artifacts"] == []


@pytest.mark.ui_live
def test_registered_runtime_and_review_evidence_is_hash_verified(ui_live_evidence) -> None:
    """Verify local history only in the explicitly enabled live lane."""
    del ui_live_evidence
    _, annotations = load_dataset()
    artifacts = [
        artifact
        for row in annotations["cases"].values()
        for lane in (row["automatic"], row["human_assisted"])
        if lane is not None
        for artifact in lane["artifacts"]
    ]
    missing = [artifact["path"] for artifact in artifacts if not (ROOT / artifact["path"]).is_file()]
    if missing:
        pytest.skip(f"Registered local UI evidence is not present: {missing[0]}")
    for case_id, row in annotations["cases"].items():
        automatic = row["automatic"]
        human = row["human_assisted"]
        if automatic is None:
            continue
        automatic_paths = {artifact["path"] for artifact in automatic["artifacts"]}
        human_paths = {artifact["path"] for artifact in human["artifacts"]}
        assert automatic_paths.isdisjoint(human_paths), case_id
        for lane in (automatic, human):
            for artifact in lane["artifacts"]:
                path = (ROOT / artifact["path"]).resolve()
                assert path.is_relative_to(ROOT.resolve())
                assert path.is_file(), artifact["path"]
                assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
