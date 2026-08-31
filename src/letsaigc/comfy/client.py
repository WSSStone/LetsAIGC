from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
from websockets.sync.client import connect

from ..errors import RuntimeExecutionError


class ComfyClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8188", timeout: float = 30.0) -> None:
        parsed = httpx.URL(base_url)
        if parsed.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("ComfyUI must use a loopback host")
        self.base_url = str(parsed).rstrip("/")
        self.timeout = timeout
        self.client_id = str(uuid.uuid4())

    def _get(self, path: str) -> Any:
        try:
            response = httpx.get(f"{self.base_url}{path}", timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeExecutionError(
                f"ComfyUI GET failed: {path}", details={"error": str(exc)}
            ) from exc

    def system_stats(self) -> dict:
        return self._get("/system_stats")

    def object_info(self) -> dict:
        return self._get("/object_info")

    def queue(self) -> dict:
        return self._get("/queue")

    def history(self, prompt_id: str) -> dict:
        return self._get(f"/history/{prompt_id}")

    def submit(self, prompt: dict) -> str:
        try:
            response = httpx.post(
                f"{self.base_url}/prompt",
                json={"prompt": prompt, "client_id": self.client_id},
                timeout=self.timeout,
            )
            if response.is_error:
                try:
                    details = response.json()
                except ValueError:
                    details = {"status_code": response.status_code, "body": response.text}
                raise RuntimeExecutionError("ComfyUI prompt submission failed", details=details)
            payload = response.json()
        except RuntimeExecutionError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeExecutionError(
                "ComfyUI prompt submission failed", details={"error": str(exc)}
            ) from exc
        if payload.get("node_errors"):
            raise RuntimeExecutionError(
                "ComfyUI rejected workflow nodes", details=payload["node_errors"]
            )
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise RuntimeExecutionError("ComfyUI response did not contain prompt_id")
        return str(prompt_id)

    def wait(self, prompt_id: str, *, timeout_seconds: int, poll_seconds: float = 1.0) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            payload = self.history(prompt_id)
            if prompt_id in payload:
                record = payload[prompt_id]
                status = record.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeExecutionError("ComfyUI execution failed", details=record)
                if status.get("completed") is True or record.get("outputs"):
                    return record
            time.sleep(poll_seconds)
        raise RuntimeExecutionError(f"Timed out waiting for prompt {prompt_id}")

    def status(self) -> dict:
        return {
            "base_url": self.base_url,
            "system_stats": self.system_stats(),
            "object_count": len(self.object_info()),
            "queue": self.queue(),
        }

    def websocket_probe(self) -> dict:
        url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        url = f"{url}/ws?clientId={self.client_id}"
        try:
            with connect(url, open_timeout=self.timeout, close_timeout=2):
                return {"url": url, "connected": True}
        except OSError as exc:
            raise RuntimeExecutionError(
                "ComfyUI WebSocket connection failed", details={"error": str(exc)}
            ) from exc
