from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import letsaigc.training.runner as runner
from letsaigc.training.adapters import register_adapter


def test_training_command_maps_harness_fields(monkeypatch) -> None:
    monkeypatch.setattr(runner, "_base_model_path", lambda _model_id: Path("model.safetensors"))
    command = runner.build_training_command(
        {
            "schema_version": 1,
            "id": "smoke",
            "base_model": "sdxl-base-1.0",
            "dataset_dir": "datasets/smoke",
            "output_dir": ".local/output",
            "train_text_encoder": False,
            "gradient_checkpointing": True,
            "resource_budget": {},
        }
    )

    assert "--train_data_dir" in command
    assert "--dataset_dir" not in command
    assert "--network_train_unet_only" in command
    assert "--gradient_checkpointing" in command


def test_adapter_record_reads_dvc_directory_hash(tmp_path, monkeypatch) -> None:
    root = tmp_path
    dataset = root / "datasets/smoke"
    dataset.mkdir(parents=True)
    (root / "registry/adapters").mkdir(parents=True)
    (root / "datasets/smoke.dvc").write_text(
        "outs:\n- md5: abc123.dir\n  path: smoke\n", encoding="utf-8"
    )
    adapter = root / "adapter.safetensors"
    adapter.write_bytes(b"adapter")
    config_path = root / "config.yaml"
    config_path.write_text("id: smoke\n", encoding="utf-8")
    monkeypatch.setattr("letsaigc.training.adapters.find_repo_root", lambda: root)
    monkeypatch.setattr(
        "letsaigc.training.adapters.load_catalog",
        lambda: SimpleNamespace(
            by_id=lambda: {
                "sdxl-base-1.0": SimpleNamespace(
                    files=[SimpleNamespace(sha256="a" * 64)]
                )
            }
        ),
    )

    record_path = register_adapter(
        adapter,
        config_path,
        {"id": "smoke", "base_model": "sdxl-base-1.0", "dataset_dir": "datasets/smoke"},
        "run-1",
    )

    assert '"dataset_dvc_rev": "abc123.dir"' in record_path.read_text(encoding="utf-8")
    assert f'"base_model_sha256": [\n    "{"a" * 64}"' in record_path.read_text(
        encoding="utf-8"
    )
