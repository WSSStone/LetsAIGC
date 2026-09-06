"""Recheck actual approved manual outputs without repeating paid inference."""

import hashlib
import json

import pytest
from PIL import Image

pytestmark = pytest.mark.ui_live


def test_three_approved_manual_runs_have_settled_outputs(ui_live_evidence):
    root, runs = ui_live_evidence
    approved = json.loads((root / "user-authorization.json").read_text(encoding="utf-8"))
    plans = {plan["task_id"]: plan for plan in approved["plans"]}
    assert len(runs) == len(plans) == 3
    assert {run["task_id"] for run in runs} == plans.keys()
    total = 0
    for run in runs:
        plan = plans[run["task_id"]]
        assert run["state"] == "succeeded" and run["stop_reason"] is None
        assert run["plan_fingerprint"] == plan["plan_fingerprint"]
        assert run["usage"]["reserved"]["cost_usd"] == run["usage"]["unsettled"]["cost_usd"] == 0
        actual = run["usage"]["actual"]["cost_usd"]
        assert 0 < actual <= plan["budget"]["max_total_cost_usd"]
        total += actual
        assert run["usage"]["actual"]["gpu_minutes"] == 0
        analyze = [op for op in run["operations"] if op["step_id"] == "analyze"]
        assert len(analyze) == 1 and analyze[0]["state"] == "succeeded"
        assert analyze[0]["provider_request_id"] == run["analysis"]["response_id"]
        assert 0 < analyze[0]["actual"]["cost_usd"] <= plan["budget"]["max_iteration_cost_usd"]
        assert run["analysis"]["usage"]["input_tokens"] > 0
        assert run["analysis"]["usage"]["output_tokens"] > 0
        assert run["ocr_jobs"] and all(job["state"] == "succeeded" for job in run["ocr_jobs"])
        assert not any(op["step_id"] == "search" for op in run["operations"])
        output = root / "results" / run["case_id"]
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["evidence_kind"] == "runtime"
        assert manifest["plan_fingerprint"] == plan["plan_fingerprint"]
        assert manifest["quality_status"] == "pending"
        for ref in manifest["outputs"]:
            suffix = ".png" if ref["media_type"] == "image/png" else ".json"
            data = (output / (ref["role"] + suffix)).read_bytes()
            assert hashlib.sha256(data).hexdigest() == ref["sha256"]
    assert total <= approved["aggregate_max_cost_usd"]


def test_live_rectangles_match_canonical_pixels(ui_live_evidence):
    root, runs = ui_live_evidence
    for run in runs:
        output = root / "results" / run["case_id"]
        index = json.loads((output / "asset_index.json").read_text(encoding="utf-8"))
        layout = json.loads((output / "layout.json").read_text(encoding="utf-8"))
        texts = json.loads((output / "texts.json").read_text(encoding="utf-8"))
        assert len(index["crops"]) == len(layout["elements"]) > 0
        assert len(texts["texts"]) > 0
        with Image.open(output / "canonical.png") as canonical, Image.open(output / "overlay.png") as overlay:
            assert overlay.size == canonical.size == (layout["width"], layout["height"])
            for number, crop in enumerate(index["crops"], 1):
                path = output / "crops" / f"{number:03}.png"
                assert hashlib.sha256(path.read_bytes()).hexdigest() == crop["ref"]["sha256"]
                with Image.open(path) as actual:
                    expected = canonical.crop(crop["bbox"])
                    assert actual.size == expected.size
                    assert actual.convert("RGBA").tobytes() == expected.convert("RGBA").tobytes()
