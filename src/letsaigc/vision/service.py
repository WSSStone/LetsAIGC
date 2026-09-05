"""Loopback OCR service: persistent provider receipts, one inference at a time."""

import argparse
import hmac
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from ..assets.store import ArtifactStore
from ..config import get_setting
from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, Identifier, canonical_json, digest
from .base import OCRJob, verify_permit

LOGGER = logging.getLogger(__name__)


def configure_paddlex_cache(root: Path, relative: str, *, environment=None) -> Path:
    """Keep PaddleX cache and temporary files inside the controlled runtime root."""
    environment = os.environ if environment is None else environment
    source = Path(relative)
    target = (root / source).resolve()
    if source.is_absolute() or not target.is_relative_to(root.resolve()):
        raise PipelineError("cache_root", "PaddleX cache must stay inside the runtime root")
    target.mkdir(parents=True, exist_ok=True)
    environment["PADDLE_PDX_CACHE_HOME"] = str(target)
    # Models are supplied by the verified lock; imports must not probe or download.
    environment["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    return target


class VisionJobs:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "receipts.sqlite"
        self.outputs = ArtifactStore(self.root / "outputs")
        with self.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                operation_id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                task_id TEXT NOT NULL, payload_hash TEXT NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL, output_ref TEXT, error_code TEXT)""")
            db.execute("UPDATE jobs SET state='unknown' WHERE state IN ('accepted','running','cancel_requested')")

    @contextmanager
    def transaction(self):
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            yield db

    @staticmethod
    def summary(row):
        if row is None:
            return None
        result = {
            name: row[name] for name in ("operation_id", "request_id", "task_id", "payload_hash", "state", "error_code")
        }
        result["outputs"] = []
        if row["output_ref"]:
            ref = ArtifactRef.model_validate_json(row["output_ref"])
            result["outputs"] = [
                {"output_id": "texts", "sha256": ref.sha256, "size_bytes": ref.size_bytes, "media_type": ref.media_type}
            ]
        result["actual"] = {"cost_usd": 0, "gpu_minutes": 0} if row["state"] in {"succeeded", "failed"} else None
        return result

    def accept(self, job: OCRJob):
        job = OCRJob.model_validate(job.model_dump(mode="json"))
        payload_hash = digest(job)
        with self.transaction() as db:
            old = db.execute("SELECT * FROM jobs WHERE operation_id=?", (job.operation_id,)).fetchone()
            if old:
                if old["payload_hash"] != payload_hash:
                    raise PipelineError("request_conflict")
                return self.summary(old)
            if db.execute(
                "SELECT 1 FROM jobs WHERE state IN ('accepted','running','cancel_requested','unknown')"
            ).fetchone():
                raise PipelineError("resource_busy")
            request_id = "vision-" + uuid4().hex
            db.execute(
                "INSERT INTO jobs(operation_id,request_id,task_id,payload_hash,payload,state) "
                "VALUES(?,?,?,?,?,'accepted')",
                (job.operation_id, request_id, job.task_id, payload_hash, canonical_json(job)),
            )
            return self.summary(db.execute("SELECT * FROM jobs WHERE operation_id=?", (job.operation_id,)).fetchone())

    def operation(self, operation_id: str, *, task_id: str):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE operation_id=?", (operation_id,)).fetchone()
            if row and row["task_id"] != task_id:
                raise PipelineError("operation_scope")
            return self.summary(row)

    def job(self, request_id: str, *, task_id: str):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE request_id=?", (request_id,)).fetchone()
            if row and row["task_id"] != task_id:
                raise PipelineError("operation_scope")
            return self.summary(row)

    def start(self, request_id: str) -> bool:
        with self.transaction() as db:
            return bool(
                db.execute(
                    "UPDATE jobs SET state='running' WHERE request_id=? AND state='accepted'", (request_id,)
                ).rowcount
            )

    def finish(self, request_id: str, *, data: bytes | None = None, error_code: str | None = None):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE request_id=?", (request_id,)).fetchone()
            if not row or row["state"] not in {"running", "cancel_requested"}:
                raise PipelineError("operation_state")
            ref = self.outputs.put(row["task_id"], request_id, data, role="texts") if data is not None else None
            db.execute(
                "UPDATE jobs SET state=?,output_ref=?,error_code=? WHERE request_id=?",
                ("failed" if error_code else "succeeded", canonical_json(ref) if ref else None, error_code, request_id),
            )

    def output(self, request_id: str, output_id: str, *, task_id: str) -> bytes:
        if output_id != "texts":
            raise PipelineError("not_found")
        self.job(request_id, task_id=task_id)
        with self.transaction() as db:
            row = db.execute("SELECT output_ref FROM jobs WHERE request_id=?", (request_id,)).fetchone()
        if not row or not row[0]:
            raise PipelineError("not_found")
        return self.outputs.read(ArtifactRef.model_validate_json(row[0]))

    def cancel(self, request_id: str, *, task_id: str):
        self.job(request_id, task_id=task_id)
        with self.transaction() as db:
            db.execute(
                "UPDATE jobs SET state='failed',error_code='cancelled_before_inference' "
                "WHERE request_id=? AND state='accepted'",
                (request_id,),
            )
            db.execute("UPDATE jobs SET state='cancel_requested' WHERE request_id=? AND state='running'", (request_id,))
        return self.job(request_id, task_id=task_id)

    def busy(self):
        with self.transaction() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM jobs WHERE state IN ('accepted','running','cancel_requested','unknown')"
                ).fetchone()
            )


class VisionApplication:
    def __init__(self, root, artifacts, *, token, signing_key, engine, model_digest):
        if not token or len(signing_key) < 32:
            raise PipelineError("vision_not_ready", "Configure local authentication and signing material")
        self.jobs = VisionJobs(root)
        self.artifacts, self.token, self.signing_key = artifacts, token, signing_key
        self.engine, self.model_digest = engine, model_digest
        self._lock = threading.Lock()

    def dispatch(self, method, path, headers, body):
        if not hmac.compare_digest(headers.get("Authorization", ""), "Bearer " + self.token):
            return 401, {"error_code": "authentication_required"}
        try:
            if method == "GET" and path == "/v1/health":
                return 200, {
                    "protocol_version": 1,
                    "capability": "ocr",
                    "device": "cpu",
                    "model_digest": self.model_digest,
                    "ready": self.engine is not None,
                    "reason": None if self.engine else "model_not_ready",
                }
            task_id = TypeAdapter(Identifier).validate_python(headers.get("X-Task-ID", ""))
            parts = path.strip("/").split("/")
            if method == "POST" and path == "/v1/jobs":
                job = OCRJob.model_validate_json(body)
                if task_id != job.task_id:
                    raise PipelineError("operation_scope")
                permit = verify_permit(headers.get("X-Execution-Permit", ""), job, self.signing_key)
                if self.engine is None or job.model_digest != self.model_digest:
                    raise PipelineError("model_not_ready")
                self.artifacts.read(job.view_ref)
                from ..ui_analysis.resources import check_storage

                check_storage(
                    self.jobs.outputs, task_id, permit.resources, extra_bytes=8 * 1024**2, memory_bytes=64 * 1024**2
                )
                with self._lock:
                    old = self.jobs.operation(job.operation_id, task_id=task_id)
                    receipt = self.jobs.accept(job)
                    if old is None:
                        threading.Thread(target=self._run, args=(job, receipt["request_id"]), daemon=True).start()
                return 202, receipt
            if len(parts) == 3 and parts[:2] == ["v1", "operations"] and method == "GET":
                result = self.jobs.operation(TypeAdapter(Identifier).validate_python(parts[2]), task_id=task_id)
            elif len(parts) >= 3 and parts[:2] == ["v1", "jobs"]:
                identity = TypeAdapter(Identifier).validate_python(parts[2])
                if len(parts) == 3 and method == "GET":
                    result = self.jobs.job(identity, task_id=task_id)
                elif len(parts) == 5 and parts[3] == "outputs" and method == "GET":
                    return 200, self.jobs.output(identity, parts[4], task_id=task_id)
                elif len(parts) == 4 and parts[3] == "cancel" and method == "POST":
                    result = self.jobs.cancel(identity, task_id=task_id)
                else:
                    raise PipelineError("not_found")
            elif path == "/v1/models/release" and method == "POST":
                with self._lock:
                    if self.jobs.busy():
                        raise PipelineError("resource_busy")
                    if self.engine:
                        self.engine.release()
                        self.engine = None
                return 200, {"released": True, "device": "cpu"}
            else:
                raise PipelineError("not_found")
            return (200, result) if result else (404, {"error_code": "not_found"})
        except PipelineError as exc:
            status = (
                403 if exc.code in {"operation_scope", "invalid_permit"} else 404 if exc.code == "not_found" else 409
            )
            return status, {"error_code": exc.code}
        except Exception:
            return 400, {"error_code": "invalid_request"}

    def _run(self, job, request_id):
        if not self.jobs.start(request_id):
            return
        try:
            data = canonical_json(self.engine.recognize(self.artifacts, job)).encode()
            if len(data) > 8 * 1024**2:
                raise PipelineError("output_limit")
            self.jobs.finish(request_id, data=data)
        except Exception:
            # The request body and recognized text are deliberately omitted;
            # the traceback is retained in the local service log for diagnosis.
            LOGGER.exception("OCR operation %s failed", job.operation_id)
            self.jobs.finish(request_id, error_code="ocr_failed")


def serve(application: VisionApplication, *, port: int = 8766):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass  # Never record credentials, URL components or model bodies.

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def handle_request(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 <= length <= 65536 or self.headers.get("Transfer-Encoding"):
                    self.send_error(413)
                    return
                body = self.rfile.read(length)
                code, result = application.dispatch(self.command, self.path, self.headers, body)
                data = result if isinstance(result, bytes) else canonical_json(result).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (OSError, ValueError):
                self.close_connection = True

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main():
    import yaml

    from ..paths import find_repo_root
    from .ocr import PaddleOCREngine

    parser = argparse.ArgumentParser()
    parser.add_argument("--capability", choices=["ocr"], required=True)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    root = find_repo_root()
    config = yaml.safe_load((root / "configs/runtime/vision.yaml").read_text())["ocr"]
    lock = yaml.safe_load((root / config["lock"]).read_text())
    configure_paddlex_cache(root, config["cache_root"])
    token = get_setting(config["token_env"], "")
    key = get_setting(config["signing_key_env"], "")
    if not token or len(key) < 32:
        parser.error("Configure vision authentication and signing material before starting")
    try:
        engine = PaddleOCREngine(lock, root)
    except PipelineError:
        engine = None
    application = VisionApplication(
        root / config["job_root"],
        ArtifactStore(root / config["artifact_root"]),
        token=token,
        signing_key=key,
        engine=engine,
        model_digest=digest(lock),
    )
    serve(application, port=args.port)


if __name__ == "__main__":
    main()
