from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from letsaigc.comfy import ComfyClient


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/system_stats":
            self._json({"system": {"os": "test"}})
        elif self.path == "/object_info":
            self._json({"KSampler": {}})
        elif self.path == "/queue":
            self._json({"queue_running": [], "queue_pending": []})
        elif self.path == "/history/prompt-test":
            self._json({"prompt-test": {"status": {"completed": True}, "outputs": {}}})
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802
        if self.path == "/prompt":
            length = int(self.headers.get("Content-Length", 0))
            json.loads(self.rfile.read(length))
            self._json({"prompt_id": "prompt-test", "node_errors": {}})
        else:
            self.send_error(404)


def test_comfy_http_lifecycle() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = ComfyClient(f"http://127.0.0.1:{server.server_port}")
        status = client.status()
        assert status["object_count"] == 1
        prompt_id = client.submit({"1": {"class_type": "Test", "inputs": {}}})
        assert client.wait(prompt_id, timeout_seconds=2)["status"]["completed"] is True
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_non_loopback_host_is_rejected() -> None:
    try:
        ComfyClient("http://192.168.1.10:8188")
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("non-loopback ComfyUI host was accepted")
