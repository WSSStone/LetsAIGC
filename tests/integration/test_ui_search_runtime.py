"""Verify saved real search/parse evidence without making provider requests."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from PIL import Image

pytestmark = pytest.mark.ui_live


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(params=["serpapi", "tavily"])
def search_live_evidence(request):
    location = os.getenv("LETSAIGC_UI_SEARCH_EVIDENCE")
    if not location:
        pytest.skip("Set LETSAIGC_UI_SEARCH_EVIDENCE to saved real search evidence")
    root = Path(location).resolve(strict=True)
    accepted = read(root / "preview-search.json").get("accepted", {}).get(request.param)
    if accepted is None:
        pytest.skip(f"{request.param} real search/parse acceptance is incomplete")
    assert accepted["provider"] == request.param
    search = (root / accepted["search_evidence_directory"]).resolve(strict=True)
    parse = (root / accepted["parse_evidence_directory"]).resolve(strict=True)
    assert search.is_relative_to(root) and parse.is_relative_to(root)
    return accepted, search, parse


def test_real_search_image_reaches_approved_settled_parse(search_live_evidence):
    accepted, search, parse = search_live_evidence
    source_run = read(search / "execution-audit.json")[0]
    run = read(parse / "execution-audit.json")[0]
    for folder, record in ((search, source_run), (parse, run)):
        approval = read(folder / "user-authorization.json")
        plan = read(folder / "approval-plan.json")
        assert record["task_id"] == approval["task_id"] == plan["task_id"]
        assert record["plan_fingerprint"] == approval["plan_fingerprint"] == plan["plan_fingerprint"]
        assert record["usage"]["actual"]["cost_usd"] <= plan["budget"]["max_total_cost_usd"]
    assert source_run["task_id"] == accepted["search_task_id"]
    attempts = [op for op in source_run["operations"] if op["step_id"].startswith("search.attempt.")]
    assert len(attempts) == 1 and attempts[0]["state"] == "succeeded"
    assert attempts[0]["result"]["provider_request_id"] == accepted["search_request_id"]
    assert attempts[0]["result"]["quota_units"] == accepted["search_units"] == 1
    downloads = [op for op in source_run["operations"] if op["step_id"].startswith("search.download.")]
    assert len(downloads) == 1 and downloads[0]["state"] == "succeeded"
    original = next(ref for ref in downloads[0]["result"]["artifacts"] if ref["role"] == "original")
    lineage = read(parse / "lineage.json")
    assert original == lineage["original_ref"]
    references = [
        ("parse-input-manifest.json", "parse_manifest_ref"),
        ("search-provenance.json", "original_provenance_ref"),
    ]
    direct = accepted["search_task_id"] == accepted["parse_task_id"]
    if not direct:
        references.append(("intake-input-manifest.json", "intake_manifest_ref"))
    for file, key in references:
        assert hashlib.sha256((parse / file).read_bytes()).hexdigest() == lineage[key]["sha256"]
    input_manifest = read(parse / "parse-input-manifest.json")
    if direct:
        assert search == parse
        sources = read(parse / "results" / run["case_id"] / "sources.json")
        image = sources["sources"][0]["original_ref"]
        assert original == image
        assert sources["index_ref"] == lineage["parse_manifest_ref"]
        assert input_manifest["sources"][0]["source_id"] == sources["sources"][0]["source_id"]
        assert input_manifest["sources"][0]["entry_ids"] == [downloads[0]["step_id"]]
        assert input_manifest["counters"]["search_attempts"] == input_manifest["counters"]["downloads"] == 1
        assert input_manifest["counters"]["probes"] == 1 and not input_manifest["failures"]
        assert sources["sources"][0]["provenance_ref"] == lineage["original_provenance_ref"]
    else:
        image = input_manifest["sources"][0]["original_ref"]
        intake = read(parse / "intake-input-manifest.json")["sources"][0]["original_ref"]
        assert original["artifact_id"] in intake["source_ids"]
        assert intake["artifact_id"] in image["source_ids"]
    assert image["sha256"] == original["sha256"] == accepted["source_sha256"]
    assert hashlib.sha256((parse / "original.jpg").read_bytes()).hexdigest() == image["sha256"]
    assert read(parse / "search-provenance.json")["source_page"] == accepted["source_page"]
    assert run["state"] == "succeeded" and run["task_id"] == accepted["parse_task_id"]
    assert run["usage"]["reserved"]["cost_usd"] == run["usage"]["unsettled"]["cost_usd"] == 0
    assert run["usage"]["actual"]["gpu_minutes"] == 0
    if not direct:
        assert not any(op["step_id"].startswith("search") for op in run["operations"])
    analyze = [op for op in run["operations"] if op["step_id"] == "analyze"]
    assert len(analyze) == 1 and analyze[0]["state"] == "succeeded"
    assert analyze[0]["provider_request_id"] == run["analysis"]["response_id"]
    assert 0 < analyze[0]["actual"]["cost_usd"] <= plan["budget"]["max_iteration_cost_usd"]
    assert run["ocr_jobs"] and all(job["state"] == "succeeded" for job in run["ocr_jobs"])


def test_real_parse_artifacts_and_rectangle_pixels(search_live_evidence):
    _, _, parse = search_live_evidence
    run = read(parse / "execution-audit.json")[0]
    output = parse / "results" / run["case_id"]
    manifest = read(output / "manifest.json")
    assert manifest["evidence_kind"] == "runtime" and manifest["plan_fingerprint"] == run["plan_fingerprint"]
    for ref in manifest["outputs"]:
        suffix = ".png" if ref["media_type"] == "image/png" else ".json"
        assert hashlib.sha256((output / (ref["role"] + suffix)).read_bytes()).hexdigest() == ref["sha256"]
    layout, index = read(output / "layout.json"), read(output / "asset_index.json")
    analysis, texts = read(output / "analysis.json"), read(output / "texts.json")
    assert analysis["state"] == "succeeded" and analysis["error_code"] is None
    evidence_ids = {analysis["view"]["view_id"]} | {item["text_id"] for item in texts["texts"]}
    for field in ("observations", "hypotheses", "elements", "occlusions", "correction_suggestions"):
        for item in analysis["output"][field]:
            assert set(item["evidence_ids"]) <= evidence_ids
    assert len(index["crops"]) == len(layout["elements"]) > 0
    with Image.open(output / "canonical.png") as canonical, Image.open(output / "overlay.png") as overlay:
        assert canonical.size == overlay.size == (layout["width"], layout["height"])
        for number, crop in enumerate(index["crops"], 1):
            path = output / "crops" / f"{number:03}.png"
            assert hashlib.sha256(path.read_bytes()).hexdigest() == crop["ref"]["sha256"]
            with Image.open(path) as image:
                expected = canonical.crop(crop["bbox"])
                assert image.convert("RGBA").tobytes() == expected.convert("RGBA").tobytes()
