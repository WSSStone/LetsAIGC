from __future__ import annotations

from pathlib import Path

import httpx

from letsaigc.comfy import ComfyClient


def test_verified_image_upload_uses_task_subfolder(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "input.png"
    source.write_bytes(b"safe")
    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return httpx.Response(
            200,
            json={"name": "input.png", "subfolder": "letsaigc-agent/session/task", "type": "input"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = ComfyClient().upload_image(source, subfolder="letsaigc-agent/session/task")
    assert result == "letsaigc-agent/session/task/input.png"
    assert captured["data"]["type"] == "input"
    assert captured["data"]["overwrite"] == "false"


def test_timeout_cancellation_never_uses_global_interrupt(monkeypatch) -> None:
    requests = []
    prompt_id = "00000000-0000-0000-0000-000000000001"

    def fake_post(url, **kwargs):
        requests.append((url, kwargs["json"]))
        return httpx.Response(200, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    ComfyClient().cancel_owned_prompt(prompt_id)
    assert requests[-1][1] == {"prompt_id": prompt_id}
    assert requests[0][1] == {"delete": [prompt_id]}
