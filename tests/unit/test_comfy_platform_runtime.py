from types import SimpleNamespace

import pytest
import yaml

from letsaigc.comfy import runtime


def _host(monkeypatch, system, machine):
    monkeypatch.setattr(runtime.platform, "system", lambda: system)
    monkeypatch.setattr(runtime.platform, "machine", lambda: machine)


def _deployment(tmp_path, *, mac):
    config = tmp_path / "configs/runtime"
    config.mkdir(parents=True)
    lock = {
        "schema_version": 1,
        "path": ".local/runtime/ComfyUI",
        "environment": "letsaigc-comfy",
        "listen": "127.0.0.1",
        "port": 8188,
    }
    if mac:
        lock["startup"] = {"args": ["--cpu", "--disable-all-custom-nodes", "--disable-api-nodes"]}
    name = "comfyui.macos-arm64.lock.yaml" if mac else "comfyui.lock.yaml"
    (config / name).write_text(yaml.safe_dump(lock))
    checkout = tmp_path / lock["path"]
    checkout.mkdir(parents=True)
    (checkout / "main.py").write_text("# test entrypoint\n")
    (checkout / "extra_model_paths.yaml").write_text("{}\n")
    return checkout


def test_mac_missing_platform_lock_does_not_use_cuda_lock(tmp_path, monkeypatch):
    _deployment(tmp_path, mac=False)
    _host(monkeypatch, "Darwin", "arm64")
    assert runtime.comfy_lock_path(tmp_path).name == "comfyui.macos-arm64.lock.yaml"
    with pytest.raises((FileNotFoundError, ValueError)):
        runtime.load_comfy_lock(tmp_path)


@pytest.mark.parametrize("mac", [True, False])
def test_serve_uses_platform_device_without_launching_models(tmp_path, monkeypatch, mac):
    checkout = _deployment(tmp_path, mac=mac)
    _host(monkeypatch, "Darwin" if mac else "Windows", "arm64" if mac else "AMD64")
    monkeypatch.setattr(runtime, "find_repo_root", lambda: tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime.serve() == 0
    assert len(calls) == 1
    command, options = calls[0]
    assert command[:7] == [
        "conda", "run", "--no-capture-output", "-n", "letsaigc-comfy", "python", str(checkout / "main.py")
    ]
    assert command[command.index("--listen") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "8188"
    assert options["cwd"] == tmp_path
    if mac:
        assert {"--cpu", "--disable-all-custom-nodes", "--disable-api-nodes"} <= set(command)
    else:
        assert "--cpu" not in command
        assert "--extra-model-paths-config" in command


def test_serve_rejects_public_bind_before_launch(monkeypatch):
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="127.0.0.1"):
        runtime.serve(host="0.0.0.0")
