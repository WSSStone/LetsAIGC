import hashlib
import json

import yaml
from PIL import Image


def test_preview_samples_have_frozen_pixels_and_development_only_truth(ui_fixture_dir):
    manifest = yaml.safe_load((ui_fixture_dir / "preview-cases.yaml").read_text(encoding="utf-8"))
    annotations = json.loads((ui_fixture_dir / "preview-annotations.json").read_text(encoding="utf-8"))
    assert {case["case_id"] for case in manifest["cases"]} == {"hud-en-landscape", "hud-zh-dense", "hud-portrait"}
    assert manifest["acceptance_status"] == "development_only"
    assert all(case["split"] == "development" for case in manifest["cases"])
    assert len({case["group_id"] for case in manifest["cases"]}) == 3
    for case in manifest["cases"]:
        path = ui_fixture_dir / case["file"]
        assert path.resolve().is_relative_to(ui_fixture_dir.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == case["sha256"]
        truth = annotations["cases"][case["case_id"]]
        with Image.open(path) as image:
            assert image.format == "PNG"
            assert list(image.size) == case["size"]
            assert image.mode == "RGB"
            assert (image.width < image.height) == (case["orientation"] == "portrait")
            assert truth["texts"] and truth["elements"]
            identities = [row["id"] for row in truth["elements"]]
            assert len(identities) == len(set(identities))
            for item in [*truth["texts"], *truth["elements"]]:
                x0, y0, x1, y1 = item["bbox"]
                assert 0 <= x0 < x1 <= image.width
                assert 0 <= y0 < y1 <= image.height
                assert len(image.crop((x0, y0, x1, y1)).getcolors(image.width * image.height)) > 1
            assert all(text["text"] and text["element_id"] in identities for text in truth["texts"])
        assert case["source_kind"] == "original_synthetic_ui"
        assert case["license_id"] == "project-owned-test-fixture"


def test_ui_fixture_store_and_ledger_are_isolated(ui_store, ui_ledger, ui_workspace, ui_calls):
    assert ui_ledger.path.is_relative_to(ui_workspace)
    assert ui_store.root.is_relative_to(ui_workspace)
    assert not ui_calls
