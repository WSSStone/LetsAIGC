"""Legacy extended T030 evidence checks (not the 2026-09-14 MVP gate).

See docs/t030-mvp-scope.md. Keep this historical evidence format intact;
cloud whole-context outputs must not be relabeled as pixel-exact Comfy composites.

The test deliberately consumes an already-written evidence package.  It never
creates a plan, consumes an approval, starts a worker, contacts SAM/Comfy, or
submits a provider operation.  The evidence contract is intentionally strict
about identity, provenance, recovery, release, and pixel protection because
those facts cannot be reconstructed after the live run.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from letsaigc.assets.store import ArtifactStore
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _ref(value: object) -> ArtifactRef:
    return ArtifactRef.model_validate(value)


def _read_ref(store: ArtifactStore, value: object) -> tuple[ArtifactRef, bytes]:
    ref = _ref(value)
    data = store.read(ref)
    assert hashlib.sha256(data).hexdigest() == ref.sha256
    return ref, data


def _assert_ref_role(store: ArtifactStore, value: object, role: str) -> ArtifactRef:
    ref, _ = _read_ref(store, value)
    assert ref.role == role
    return ref


def _assert_zero_cost(value: dict) -> None:
    assert value["cost_usd"] >= 0
    assert value["gpu_minutes"] >= 0


def _assert_case_identity(case: dict) -> None:
    assert case["case_id"]
    assert case["commercial_ui"] is True
    source = case["source"]
    assert source["origin"] in {"licensed_local", "authorized_local", "authorized_reference"}
    assert source["license_status"] in {"verified", "approved", "restricted"}
    assert source["original_sha256"] == source["original_ref"]["sha256"]
    assert len(source["original_sha256"]) == 64

    parse = case["parse"]
    assert parse["task_id"]
    assert len(parse["plan_fingerprint"]) == 64
    assert parse["input_sha256"] == source["original_sha256"]
    assert parse["layout_ref"]["role"] == "layout"
    assert parse["texts_ref"]["role"] in {"texts", "review_texts"}
    assert parse["manifest_ref"]["role"] in {"manifest", "ui_manifest"}

    review = case["review"]
    assert review["task_id"] == parse["task_id"]
    assert review["status"] == "confirmed"
    assert isinstance(review["confirmed_revision"], int) and review["confirmed_revision"] >= 0
    assert review["layout_ref"]["role"] == "review_layout"
    assert review["texts_ref"]["role"] == "review_texts"
    assert review["manifest_ref"]["role"] == "review_manifest"


def _assert_plan_identity(plan: dict, case_ids: set[str]) -> None:
    assert plan["case_id"] in case_ids
    assert plan["mode"] in {"decompose", "reconstruct"}
    if plan["mode"] == "reconstruct":
        assert plan["target"] in {"scene_background", "map_surface"}
    assert len(plan["root"]["plan_fingerprint"]) == 64
    assert plan["root"]["review_revision"] >= 0
    assert plan["root"]["selection_ref"]["sha256"] == plan["root"]["selection_hash"]

    segment = plan["segmentation"]
    assert len(segment["plan_fingerprint"]) == 64
    assert segment["approved_fingerprint"] == segment["plan_fingerprint"]
    assert segment["approval_consumed"] is True
    assert segment["operation_id"] and segment["provider_request_id"]
    assert segment["provider_accept_count"] == 1
    assert segment["recovery_observations"] >= 1
    assert segment["release"]["acknowledged"] is True
    assert segment["release"]["cuda_allocated_bytes"] == 0

    inpaint = plan["inpaint"]
    assert len(inpaint["plan_fingerprint"]) == 64
    assert inpaint["approved_fingerprint"] == inpaint["plan_fingerprint"]
    assert inpaint["approval_consumed"] is True
    assert inpaint["operation_id"] and inpaint["provider_request_id"]
    assert inpaint["provider_accept_count"] == 1
    assert inpaint["release"]["acknowledged"] is True
    assert inpaint["release"]["cuda_allocated_bytes"] == 0


def _assert_pixels(store: ArtifactStore, quality: dict) -> None:
    original_ref, original_data = _read_ref(store, quality["original_ref"])
    mask_ref = _assert_ref_role(store, quality["mask_ref"], "edit_mask")
    generated_ref = _assert_ref_role(store, quality["generated_ref"], "generated")
    assert original_ref.sha256 == quality["original_sha256"]
    assert mask_ref.sha256 == quality["mask_sha256"]
    assert generated_ref.sha256 == quality["generated_sha256"]

    with Image.open(BytesIO(original_data)) as original:
        assert list(original.size) == quality["canonical_size"]
    diff = quality["outside_mask_pixel_diff"]
    assert diff["changed_pixels"] == 0
    assert diff["max_abs"] == 0
    assert diff["measurement"] == "canonical_rgba_outside_final_mask"
    if quality["background_truth_available"] is False:
        assert quality.get("background_accuracy") is None
    else:
        assert quality["background_accuracy"]["denominator"] > 0


@pytest.mark.ui_live
@pytest.mark.gpu
def test_t030_real_editing_evidence_is_read_only_and_complete(ui_live_evidence):
    """Verify T030 output without re-running any external capability."""

    root, audit = ui_live_evidence
    evidence_path = root / "editing-runtime.json"
    if not evidence_path.is_file():
        pytest.skip("Selected UI evidence has no T030 editing-runtime.json")

    runtime = json.loads(evidence_path.read_text(encoding="utf-8"))
    if runtime.get("acceptance_scope") != "t030_real_editing":
        pytest.skip("Selected UI evidence is not a T030 real-editing package")

    assert runtime["schema_version"] == 1
    assert runtime["formal_quality_acceptance"] is False
    assert audit["editing_runtime_sha256"] == _sha256(evidence_path)
    assert audit["acceptance_scope"] == "t030_real_editing"
    assert audit["external_calls_during_validation"] == 0

    cases = runtime["inputs"]
    assert {case["source_kind"] for case in cases} == {"commercial_ui"}
    assert len(cases) >= 3
    for case in cases:
        _assert_case_identity(case)

    case_ids = {case["case_id"] for case in cases}
    plans = runtime["plans"]
    assert any(plan["mode"] == "decompose" for plan in plans)
    assert any(plan["mode"] == "reconstruct" and plan["target"] == "scene_background" for plan in plans)
    assert any(plan["mode"] == "reconstruct" and plan["target"] == "map_surface" for plan in plans)
    for plan in plans:
        _assert_plan_identity(plan, case_ids)
        for budget in (plan["root"]["budget"], plan["segmentation"]["budget"], plan["inpaint"]["budget"]):
            assert budget["max_total_gpu_minutes"] > 0
            assert budget["max_iteration_gpu_minutes"] > 0
            assert budget["max_total_cost_usd"] > 0
            assert budget["max_iteration_cost_usd"] > 0

    calls = runtime["calls"]
    assert calls["planning"] == 0
    assert calls["review_save_confirm"] == 0
    assert calls["ocr"] >= 0
    assert calls["vlm"] >= 0
    assert calls["sam"] == sum(plan["segmentation"]["inference_calls"] for plan in plans)
    assert calls["comfy"] == sum(plan["inpaint"]["inference_calls"] for plan in plans)
    for value in runtime["actual_usage"].values():
        _assert_zero_cost(value)

    recovery = runtime["worker_recovery"]
    assert recovery["worker_restarted"] is True
    assert recovery["provider_kept_running"] is True
    assert recovery["provider_accept_count"] == 1
    assert recovery["recovery_observations"] >= 1
    assert recovery["resubmitted"] is False
    assert recovery["operation_id"] == recovery["operation_id_after_restart"]
    assert recovery["provider_request_id"] == recovery["provider_request_id_after_restart"]
    assert recovery["unknown_cost_preserved"] is True

    sc017 = runtime["sc017"]
    assert sc017["original_review_task_id"]
    assert sc017["original_confirmed_revision"] == sc017["review_head_after_unadopted_suggestion"]
    assert sc017["locked_fields_overwritten_without_adoption"] == 0
    assert sc017["raw_model_output_preserved"] is True
    assert sc017["suggestion_is_not_ground_truth"] is True
    if sc017["adopted"]:
        assert sc017["adoption_user_choice"] is True
        assert sc017["new_draft_created"] is True
        assert sc017["confirmed_revision_changed"] is False

    store = ArtifactStore(root / "artifacts")
    for quality in runtime["quality"]:
        _assert_pixels(store, quality)
        assert quality["source_labels"]
        assert set(quality["source_labels"]) >= {"original", "estimated", "generated"}

    state_root = root / "ledger"
    if state_root.is_dir() and (state_root / "ledger.sqlite").is_file():
        service = PipelineService(state_root, ui_schema=5)
        for plan in plans:
            segment = plan["segmentation"]
            operation = service.ledger.get(segment["operation_id"])
            assert operation.state == "succeeded"
            assert operation.provider_request_id == segment["provider_request_id"]
        with sqlite3.connect(state_root / "ledger.sqlite") as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
