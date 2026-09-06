"""Task-scoped loopback editor; sessions never grant pipeline execution authority."""

import json
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef
from ..schemas.ui_review import ReviewConfirm, ReviewPatch

MAX_BODY = 1024**2


class ReviewSession:
    def __init__(self, host, *, now=None):
        self.host, self.origin = host, "http://" + host
        self.bootstrap = secrets.token_urlsafe(32)
        self.created = time.monotonic() if now is None else now
        self.used = False
        self.cookie, self.csrf, self.last_seen = None, None, self.created
        self.lock = threading.Lock()

    def exchange(self, token, host, origin, *, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if host != self.host or origin != self.origin or self.used or now - self.created > 600:
                raise PipelineError("review_unauthorized")
            if not isinstance(token, str) or not secrets.compare_digest(token, self.bootstrap):
                raise PipelineError("review_unauthorized")
            self.used, self.last_seen = True, now
            self.cookie, self.csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            return self.cookie, self.csrf

    def authorize(self, cookie, host, origin, csrf, *, write, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if (
                host != self.host
                or origin not in {None, self.origin}
                or not self.cookie
                or not isinstance(cookie, str)
                or not secrets.compare_digest(cookie, self.cookie)
                or now - self.last_seen > 1800
            ):
                raise PipelineError("review_unauthorized")
            if write and (
                origin != self.origin or not isinstance(csrf, str) or not secrets.compare_digest(csrf, self.csrf)
            ):
                raise PipelineError("review_unauthorized")
            self.last_seen = now


def parse_body(data):
    if len(data) > MAX_BODY:
        raise PipelineError("review_payload_too_large")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("Nonfinite JSON value")

    value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError("Object required")
    return value


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, repo, task_id):
        self.repo, self.task_id = repo, task_id
        self.slots = threading.BoundedSemaphore(8)
        self.job_lock = threading.Lock()
        self.job = None
        super().__init__(("127.0.0.1", 0), ReviewHandler)
        self.session = ReviewSession(f"127.0.0.1:{self.server_port}")
        self.cookie_name = f"review_session_{self.server_port}"
        pending = repo.pending_confirmation(task_id)
        if pending:
            self.start_confirm(pending)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # Never log request bodies, authentication or source text.
        pass

    def artifact_refs(self):
        head = self.repo.head(self.task_id)
        values = [head["base_refs"]]
        with self.repo.ledger.transaction() as db:
            for row in db.execute(
                "SELECT draft_ref,patch_ref,published_refs FROM ui_review_revisions WHERE task_id=?", (self.task_id,)
            ):
                values.extend(json.loads(x) for x in row if x)
        found = {}

        def collect(value):
            if isinstance(value, dict):
                if {"artifact_id", "sha256", "task_id", "key"} <= value.keys():
                    ref = ArtifactRef.model_validate(value)
                    if ref.task_id != self.task_id:
                        raise PipelineError("artifact_scope")
                    found[ref.artifact_id] = ref
                else:
                    for child in value.values():
                        collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        for value in values:
            collect(value)
        # Only approved index schemas extend the closure; arbitrary JSON text is not followed.
        for ref in list(found.values()):
            if ref.role in {"review_manifest", "asset_index"}:
                collect(json.loads(self.repo.store.read(ref)))
        return found

    def start_confirm(self, request):
        with self.job_lock:
            if self.job and self.job.is_alive():
                raise PipelineError("review_confirm_busy")
            with self.repo.ledger.transaction() as db:
                self.repo._existing(db, self.task_id, request, "confirm")

            def run():
                try:
                    self.repo.confirm(self.task_id, request)
                except Exception:
                    pass  # Repository persists safe failure codes; no model text in logs.

            self.job = threading.Thread(target=run, daemon=False, name="ui-review-confirm")
            self.job.start()


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "LocalReview"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        pass

    def reply(self, status, value, *, media_type="application/json", cookie=None):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        if cookie:
            self.send_header("Set-Cookie", f"{self.server.cookie_name}={cookie}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def do_OPTIONS(self):
        self.reply(403, {"error": "review_unauthorized"})

    def dispatch(self, write):
        try:
            self.route(write)
        except PipelineError as exc:
            status = (
                403
                if exc.code in {"review_unauthorized", "artifact_scope"}
                else (
                    409
                    if exc.code in {"review_conflict", "review_request_conflict", "review_confirm_busy"}
                    else 413
                    if exc.code == "review_payload_too_large"
                    else 404
                    if exc.code.endswith("unavailable")
                    else 400
                )
            )
            self.reply(status, {"error": exc.code})
        except (ValueError, KeyError, TypeError, RecursionError):
            self.reply(400, {"error": "review_invalid_input"})
        except (BrokenPipeError, ConnectionError, TimeoutError):
            pass
        except Exception:
            self.reply(500, {"error": "review_internal_error"})

    def route(self, write):
        server = self.server
        host, origin = self.headers.get("Host"), self.headers.get("Origin")
        if (
            host != server.session.host
            or origin not in {None, server.session.origin}
            or len(self.headers.get_all("Host", [])) != 1
            or "?" in self.path
            or "%" in self.path
        ):
            raise PipelineError("review_unauthorized")
        static = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript"),
            "/styles.css": ("styles.css", "text/css"),
            "/state.js": ("state.js", "text/javascript"),
        }
        if not write and self.path in static:
            name, media = static[self.path]
            return self.reply(200, (Path(__file__).with_name("review_web") / name).read_bytes(), media_type=media)
        body = None
        if write:
            if (
                self.headers.get("Content-Type") != "application/json"
                or self.headers.get("Transfer-Encoding")
                or len(self.headers.get_all("Content-Length", [])) != 1
            ):
                raise PipelineError("review_invalid_input")
            length = int(self.headers["Content-Length"])
            if length < 0 or length > MAX_BODY:
                raise PipelineError("review_payload_too_large")
            body = parse_body(self.rfile.read(length))
        if write and self.path == "/api/session":
            if set(body) != {"bootstrap"}:
                raise PipelineError("review_invalid_input")
            cookie, csrf = server.session.exchange(body["bootstrap"], host, origin)
            return self.reply(200, {"csrf": csrf}, cookie=cookie)
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        cookie = cookies.get(server.cookie_name)
        server.session.authorize(
            cookie.value if cookie else None, host, origin, self.headers.get("X-Review-CSRF"), write=write
        )
        repo, task = server.repo, server.task_id
        if not write and self.path == "/api/review":
            head = repo.head(task)
            return self.reply(
                200,
                {
                    "head": head,
                    "document": repo.document(task, head["draft_revision"]).model_dump(mode="json"),
                    "csrf": server.session.csrf,
                    "limits": {"max_actions": 256, "undo_actions": 100},
                    "capabilities": ["local_review"],
                    "external_calls": 0,
                },
            )
        if not write and self.path.startswith("/api/artifacts/"):
            ref = server.artifact_refs().get(self.path.removeprefix("/api/artifacts/"))
            if ref is None:
                raise PipelineError("artifact_scope")
            return self.reply(200, repo.store.read(ref), media_type=ref.media_type)
        if not write and self.path.startswith("/api/review/requests/"):
            return self.reply(200, repo.request(task, self.path.removeprefix("/api/review/requests/")))
        if write and self.path == "/api/review/drafts":
            return self.reply(200, repo.save(task, ReviewPatch.model_validate(body)))
        if write and self.path == "/api/review/confirm":
            request = ReviewConfirm.model_validate(body)
            server.start_confirm(request)
            return self.reply(202, {"request_id": request.request_id, "state": "confirming"})
        raise PipelineError("review_route_unavailable")
