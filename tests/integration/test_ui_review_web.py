from pathlib import Path


def test_review_web_is_packaged_local_and_separates_save_from_confirm():
    root = Path(__file__).parents[2] / "src/letsaigc/ui_analysis/review_web"
    html = (root / "index.html").read_text()
    assert 'id="save"' in html and 'id="confirm"' in html
    assert 'id="canvas"' in html and 'id="properties"' in html
    for name in ("index.html", "app.js", "state.js", "styles.css"):
        body = (root / name).read_text()
        assert "https://" not in body and "localStorage" not in body and "innerHTML" not in body
    assert "screenToCanonical" in (root / "state.js").read_text()
    assert "beforeunload" in (root / "app.js").read_text()
