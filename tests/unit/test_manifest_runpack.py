from __future__ import annotations

import json
import zipfile
from pathlib import Path

from letsaigc.runpack import build_runpack
from letsaigc.schemas import LicenseLane
from letsaigc.tracking import add_output, create_manifest, save_manifest


def test_runpack_contains_metadata_but_not_output_bytes(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "configs/models").mkdir(parents=True)
    (root / "configs/policies").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='test'\n")
    (root / "configs/models/catalog.yaml").write_text("schema_version: 1\n")
    (root / "configs/policies/license-policy.yaml").write_text("schema_version: 1\n")
    monkeypatch.setenv("LETSAIGC_ROOT", str(root))
    output = root / ".local/output/asset.png"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"image bytes must not be packed")
    manifest = create_manifest(
        kind="inference",
        parameters={},
        license_lanes=[LicenseLane.production],
    )
    add_output(manifest, output)
    manifest.status = "succeeded"
    save_manifest(manifest)
    target = build_runpack(manifest.run_id)
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert "manifest.portable.json" in names
        assert "asset.png" not in names
        portable = json.loads(archive.read("manifest.portable.json"))
        assert portable["outputs"][0]["filename"] == "asset.png"
