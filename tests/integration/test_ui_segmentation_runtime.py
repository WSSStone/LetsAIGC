"""T024 evidence checks; pytest never starts SAM or grants approval."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from io import BytesIO

import pytest
from PIL import Image

from letsaigc.assets.store import ArtifactStore
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef


def _read_ref(store: ArtifactStore, value: dict) -> tuple[ArtifactRef, bytes]:
    ref = ArtifactRef.model_validate(value)
    data = store.read(ref)
    assert hashlib.sha256(data).hexdigest() == ref.sha256
    return ref, data


@pytest.mark.ui_live
@pytest.mark.gpu
def test_t024_real_sam_runtime_evidence(ui_live_evidence):
    root, audit = ui_live_evidence
    if not (root / "segmentation-runtime.json").is_file():
        pytest.skip("Selected UI evidence has no Windows T024 SAM runtime record")
    runtime = json.loads((root / "segmentation-runtime.json").read_text(encoding="utf-8"))
    assert runtime == audit
    assert runtime["schema_version"] == 1
    assert runtime["acceptance_scope"] == "development_fixed_sample"
    assert runtime["formal_quality_acceptance"] is False
    assert runtime["inference_calls"] == 1
    assert runtime["task_id"] != runtime["root_task_id"]
    assert len(runtime["plan_fingerprint"]) == 64
    assert runtime["approved_fingerprint"] == runtime["plan_fingerprint"]
    assert runtime["selection"]["revision"] == 0
    assert runtime["selection"]["sha256"] == runtime["request"]["selection_hash"]
    assert runtime["parameters_hash"] == runtime["request"]["parameters_hash"]
    assert runtime["model"]["repo"] == "facebook/sam2.1-hiera-large"
    assert runtime["model"]["revision"] == "665f8e2ad61cf5f53d65644ff27c8ee525124610"
    assert runtime["model"]["weight_sha256"] == (
        "dc407dce21301fd94abb395c5099b4f2c455fdc8a8f261ac3d0ea6d4cd197230"
    )
    assert runtime["model"]["torch"] == "2.9.1+cu130"
    assert runtime["model"]["torchvision"] == "0.24.1+cu130"
    assert runtime["model"]["transformers"] == "4.57.6"
    assert runtime["execution"]["state"] == "succeeded"
    assert runtime["execution"]["actual"]["cost_usd"] == 0
    assert 0 < runtime["execution"]["actual"]["gpu_minutes"] <= runtime["budget"][
        "max_iteration_gpu_minutes"
    ]
    assert runtime["execution"]["provider_accept_count"] == 1
    assert runtime["execution"]["recovery_request_count"] >= 1
    assert runtime["release"] == {"acknowledged": True, "cuda_allocated_bytes": 0}
    assert all(runtime["checks"].values())

    state_root = root / "acceptance-state"
    service = PipelineService(state_root, ui_schema=5)
    plan = service.ledger.plan(runtime["task_id"])
    assert plan.fingerprint == runtime["plan_fingerprint"]
    operation = service.ledger.get(runtime["execution"]["operation_id"])
    assert operation.state == "succeeded"
    assert operation.actual.model_dump(mode="json") == runtime["execution"]["actual"]
    assert operation.result["usage_verdict"] in {"within_budget", "reservation_adjusted"}
    assert service.ledger.usage(runtime["root_task_id"], include_children=True)["unsettled"].gpu_minutes == 0
    with sqlite3.connect(state_root / "ledger.sqlite") as db:
        approval = db.execute(
            "SELECT actor,consumed FROM approvals WHERE task_id=?", (runtime["task_id"],)
        ).fetchone()
    assert approval is not None and approval[0] and approval[1] == 1
    with sqlite3.connect(state_root / "provider" / "receipts.sqlite") as db:
        accepted = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE operation_id=?", (operation.operation_id,)
        ).fetchone()[0]
    assert accepted == 1

    store = ArtifactStore(state_root / "artifacts")
    canonical_ref, canonical_bytes = _read_ref(store, runtime["canonical_ref"])
    selection_ref, _ = _read_ref(store, runtime["selection"]["ref"])
    model_ref, _ = _read_ref(store, runtime["model_snapshot_ref"])
    assert canonical_ref.sha256 == runtime["canonical_sha256"]
    assert selection_ref.sha256 == runtime["selection"]["sha256"]
    assert model_ref.sha256 == runtime["model"]["snapshot_digest"]
    bundle_ref, bundle_bytes = _read_ref(store, runtime["segmentation_ref"])
    assert bundle_ref.role == "segmentation"
    bundle = json.loads(bundle_bytes)
    assert bundle["task_id"] == runtime["task_id"]
    assert bundle["operation_id"] == operation.operation_id
    assert bundle["selection_hash"] == selection_ref.sha256
    assert bundle["model_digest"] == model_ref.sha256

    with Image.open(BytesIO(canonical_bytes)) as source_image:
        canonical = source_image.convert("RGBA")
    assert list(canonical.size) == runtime["canonical_size"]
    for item in bundle["items"]:
        rect_ref, rect_data = _read_ref(store, item["rect_crop_ref"])
        mask_ref, mask_data = _read_ref(store, item["contour_mask_ref"])
        alpha_ref, alpha_data = _read_ref(store, item["estimated_alpha_ref"])
        visible_ref, visible_data = _read_ref(store, item["visible_crop_ref"])
        assert [rect_ref.role, mask_ref.role, alpha_ref.role, visible_ref.role] == [
            "rect_crop",
            "contour_mask",
            "estimated_alpha",
            "visible_crop",
        ]
        x0, y0, x1, y1 = item["bbox"]
        with Image.open(BytesIO(rect_data)) as rect, Image.open(BytesIO(mask_data)) as mask:
            assert rect.size == (x1 - x0, y1 - y0)
            assert mask.mode == "L" and mask.size == canonical.size
        with Image.open(BytesIO(alpha_data)) as alpha, Image.open(BytesIO(visible_data)) as visible:
            assert alpha.mode == "L" and alpha.size == canonical.size
            output_alpha = visible.convert("RGBA").getchannel("A").tobytes()
            input_alpha = canonical.crop((x0, y0, x1, y1)).getchannel("A").tobytes()
            assert all(output <= original for output, original in zip(output_alpha, input_alpha, strict=True))

    glyph = runtime["glyph"]
    assert glyph["status"] in {"estimated", "low_confidence"}
    glyph_mask_ref, _ = _read_ref(store, glyph["glyph_mask_ref"])
    glyph_image_ref, _ = _read_ref(store, glyph["glyph_image_ref"])
    provenance_ref, provenance_data = _read_ref(store, glyph["provenance_ref"])
    assert [glyph_mask_ref.role, glyph_image_ref.role, provenance_ref.role] == [
        "glyph_mask",
        "glyph_image",
        "provenance",
    ]
    provenance = json.loads(provenance_data)
    assert provenance["kind"] == "glyph_asset"
    assert provenance["epistemic_status"] == "estimated"
    assert provenance["method"] != "sam"
    assert glyph["text_id"] not in {item["element_id"] for item in bundle["items"]}
