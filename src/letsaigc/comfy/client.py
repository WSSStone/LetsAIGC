from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from websockets.sync.client import connect

from ..config import get_setting
from ..errors import RuntimeExecutionError

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
PINNED_COMFYUI_VERSION = "0.34.2"


class ComfyClient:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        parsed = httpx.URL(base_url or get_setting("COMFY_URL", DEFAULT_COMFY_URL))
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
            raise RuntimeExecutionError(f"ComfyUI GET failed: {path}", details={"error": str(exc)}) from exc

    def system_stats(self) -> dict:
        return self._get("/system_stats")

    def object_info(self) -> dict:
        return self._get("/object_info")

    def queue(self) -> dict:
        return self._get("/queue")

    def history(self, prompt_id: str) -> dict:
        return self._get(f"/history/{prompt_id}")

    def submit(self, prompt: dict, *, extra_data: dict | None = None) -> str:
        try:
            response = httpx.post(
                f"{self.base_url}/prompt",
                json={
                    "prompt": prompt,
                    "client_id": self.client_id,
                    **({"extra_data": extra_data} if extra_data else {}),
                },
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
            raise RuntimeExecutionError("ComfyUI prompt submission failed", details={"error": str(exc)}) from exc
        if payload.get("node_errors"):
            raise RuntimeExecutionError("ComfyUI rejected workflow nodes", details=payload["node_errors"])
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise RuntimeExecutionError("ComfyUI response did not contain prompt_id")
        return str(prompt_id)

    def upload_image(self, path: Path, *, subfolder: str) -> str:
        if not path.is_file():
            raise RuntimeExecutionError(f"ComfyUI upload source is missing: {path}")
        normalized = subfolder.replace("\\", "/").strip("/")
        if not normalized or ".." in normalized.split("/"):
            raise RuntimeExecutionError("ComfyUI upload subfolder is unsafe")
        try:
            with path.open("rb") as handle:
                response = httpx.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (path.name, handle, "application/octet-stream")},
                    data={"subfolder": normalized, "type": "input", "overwrite": "false"},
                    timeout=self.timeout,
                )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, OSError) as exc:
            raise RuntimeExecutionError("ComfyUI image upload failed", details={"error": str(exc)}) from exc
        name = payload.get("name")
        returned_subfolder = str(payload.get("subfolder", normalized)).replace("\\", "/").strip("/")
        name_path = Path(str(name))
        if (
            not name
            or name_path.is_absolute()
            or name_path.name != str(name)
            or ".." in name_path.parts
            or returned_subfolder != normalized
        ):
            raise RuntimeExecutionError("ComfyUI returned an unsafe upload location")
        return f"{returned_subfolder}/{name}"

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

    def cancel_owned_prompt(self, prompt_id: str) -> None:
        """Cancel only this submitted job (supported by the pinned 0.34.2 server)."""
        try:
            uuid.UUID(prompt_id)
            for route, payload in (
                ("/queue", {"delete": [prompt_id]}),
                ("/interrupt", {"prompt_id": prompt_id}),
            ):
                response = httpx.post(f"{self.base_url}{route}", json=payload, timeout=self.timeout)
                response.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeExecutionError("Could not confirm cancellation of the owned ComfyUI job") from exc

    @staticmethod
    def _queue_prompt_ids(payload: Any) -> list[str]:
        """Return queue IDs, rejecting an unverifiable queue response."""
        if not isinstance(payload, dict):
            raise RuntimeExecutionError("ComfyUI queue response is not an object")
        prompt_ids: list[str] = []
        for key in ("queue_running", "queue_pending"):
            entries = payload.get(key)
            if not isinstance(entries, list):
                raise RuntimeExecutionError("ComfyUI queue response is malformed")
            for entry in entries:
                if (
                    not isinstance(entry, list)
                    or len(entry) < 2
                    or not isinstance(entry[1], str)
                    or not entry[1]
                ):
                    raise RuntimeExecutionError("ComfyUI queue response contains an unverifiable job")
                prompt_ids.append(entry[1])
        return prompt_ids

    @staticmethod
    def _cuda_release_proof(payload: Any) -> dict[str, Any]:
        """Validate the pinned server's CUDA allocation counters."""
        if not isinstance(payload, dict):
            raise RuntimeExecutionError("ComfyUI system stats response is not an object")
        system = payload.get("system")
        if not isinstance(system, dict) or system.get("comfyui_version") != PINNED_COMFYUI_VERSION:
            raise RuntimeExecutionError("ComfyUI release proof requires the pinned server version")
        devices = payload.get("devices")
        if not isinstance(devices, list):
            raise RuntimeExecutionError("ComfyUI system stats devices are unavailable")
        cuda_devices = [device for device in devices if isinstance(device, dict) and device.get("type") == "cuda"]
        if not cuda_devices:
            raise RuntimeExecutionError("ComfyUI CUDA allocation is not observable")
        proof_devices: list[dict[str, Any]] = []
        for device in cuda_devices:
            total = device.get("torch_vram_total")
            free = device.get("torch_vram_free")
            if type(total) is not int or type(free) is not int or total < 0 or free < 0 or free > total:
                raise RuntimeExecutionError("ComfyUI CUDA allocation counters are malformed")
            if total - free != 0:
                raise RuntimeExecutionError("ComfyUI still reports active CUDA allocation")
            index = device.get("index")
            if index is not None and type(index) is not int:
                raise RuntimeExecutionError("ComfyUI CUDA device index is malformed")
            proof_devices.append({
                "index": index,
                "name": device.get("name") if isinstance(device.get("name"), str) else None,
                "torch_vram_total": total,
                "torch_vram_free": free,
                "torch_vram_allocated": 0,
            })
        return {
            "released": True,
            "comfyui_version": PINNED_COMFYUI_VERSION,
            "devices": proof_devices,
        }

    def release_models(self, *, timeout_seconds: float | None = None, poll_seconds: float = 0.1) -> dict[str, Any]:
        """Unload native Comfy models and prove that CUDA allocations reached zero.

        The pinned ``/free`` endpoint only sets asynchronous queue flags.  A
        successful HTTP response therefore does not release this operation's
        local-gpu lease; release succeeds only after an empty queue and
        version-pinned CUDA counters report ``torch_vram_total -
        torch_vram_free == 0``.
        """
        if type(poll_seconds) not in {int, float} or isinstance(poll_seconds, bool) or poll_seconds < 0:
            raise RuntimeExecutionError("ComfyUI release polling interval is invalid")
        release_timeout = self.timeout if timeout_seconds is None else timeout_seconds
        if type(release_timeout) not in {int, float} or isinstance(release_timeout, bool) or release_timeout <= 0:
            raise RuntimeExecutionError("ComfyUI release timeout is invalid")

        # Do not ask ComfyUI to unload resources while another operation is
        # queued.  This is a local ownership guard, not a global interrupt.
        before = self._queue_prompt_ids(self.queue())
        if before:
            raise RuntimeExecutionError("Cannot release ComfyUI models while queue jobs exist")
        try:
            response = httpx.post(
                f"{self.base_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeExecutionError("ComfyUI model release request failed", details={"error": str(exc)}) from exc

        deadline = time.monotonic() + float(release_timeout)
        while True:
            after = self._queue_prompt_ids(self.queue())
            if after:
                raise RuntimeExecutionError("ComfyUI queue became busy during model release")
            try:
                proof = self._cuda_release_proof(self.system_stats())
            except RuntimeExecutionError as exc:
                # Version/device/schema failures are permanent proof gaps for
                # this release attempt.  Only an observed non-zero allocation
                # is expected to settle asynchronously after /free.
                if "still reports active CUDA allocation" not in str(exc):
                    raise
                if time.monotonic() >= deadline:
                    raise
                if poll_seconds:
                    time.sleep(min(float(poll_seconds), max(0.0, deadline - time.monotonic())))
                continue
            # A final queue read closes the race between the proof and handing
            # the resource back to the ledger.
            final = self._queue_prompt_ids(self.queue())
            if final:
                raise RuntimeExecutionError("ComfyUI queue became busy before model release completed")
            return proof

    # The coordinator searches for this generic hook when it owns a native
    # Comfy backend.  Keep the explicit name above for callers that want to
    # distinguish model release from queue cancellation.
    release = release_models

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
            raise RuntimeExecutionError("ComfyUI WebSocket connection failed", details={"error": str(exc)}) from exc
