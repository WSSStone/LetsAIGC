import json

import pytest

from letsaigc.ui_analysis.runtime import vision_settings
from letsaigc.vision.settings import load_ocr_settings


@pytest.mark.parametrize(
    ("system", "machine", "expected"), [("Darwin", "arm64", "mac"), ("Windows", "AMD64", "windows")]
)
def test_planner_and_service_choose_same_platform_lock(tmp_path, monkeypatch, system, machine, expected):
    config_dir = tmp_path / "configs/runtime"
    config_dir.mkdir(parents=True)
    config = {
        "lock": "configs/runtime/windows.yaml",
        "platform_locks": {"Darwin-arm64": "configs/runtime/mac.yaml"},
    }
    (config_dir / "vision.yaml").write_text(json.dumps({"ocr": config}))
    for name in ("windows", "mac"):
        (config_dir / f"{name}.yaml").write_text(json.dumps({"wheel_platform": name}))
    monkeypatch.setenv("LETSAIGC_ROOT", str(tmp_path))
    monkeypatch.setattr("letsaigc.vision.settings.platform.system", lambda: system)
    monkeypatch.setattr("letsaigc.vision.settings.platform.machine", lambda: machine)
    assert load_ocr_settings(tmp_path) == vision_settings() == (config, {"wheel_platform": expected})
    if expected == "mac":
        (config_dir / "mac.yaml").unlink()
        with pytest.raises(OSError):
            load_ocr_settings(tmp_path)  # A missing selected lock cannot fall back to a different wheel.
