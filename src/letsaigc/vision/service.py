"""Loopback OCR service: persistent provider receipts, one inference at a time."""

import argparse
import hmac
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from ..assets.store import ArtifactStore
from ..config import get_setting
from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, Identifier, canonical_json, digest
from .base import OCRJob, SAMJob, verify_permit, verify_sam_permit

LOGGER = logging.getLogger(__name__)


def validation_runtime_paths(root: Path, value: str) -> tuple[Path, Path]:
    """Resolve an explicit T024-style local validation root without arbitrary writes."""

    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise PipelineError("unsafe_path", "Validation root must be relative and local")
    base = (root / ".local/validation/ui-analysis").resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise PipelineError("unsafe_path", "Validation root escapes local UI evidence")
    return target / "artifacts", target / "provider"


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
                state TEXT NOT NULL, output_ref TEXT, error_code TEXT,
                capability TEXT NOT NULL DEFAULT 'ocr', actual_cost REAL,
                actual_gpu REAL)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
            if "capability" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN capability TEXT NOT NULL DEFAULT 'ocr'")
            if "actual_cost" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN actual_cost REAL")
            if "actual_gpu" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN actual_gpu REAL")
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
            name: row[name]
            for name in (
                "operation_id",
                "request_id",
                "task_id",
                "payload_hash",
                "state",
                "error_code",
                "capability",
            )
        }
        # Keep the established OCR v1 receipt shape stable.  Segmentation
        # receipts carry the capability discriminator because their output and
        # settlement semantics differ from OCR.
        if row["capability"] == "ocr":
            result.pop("capability", None)
        result["outputs"] = []
        if row["output_ref"]:
            ref = ArtifactRef.model_validate_json(row["output_ref"])
            result["outputs"] = [
                {
                    "output_id": ref.role,
                    "sha256": ref.sha256,
                    "size_bytes": ref.size_bytes,
                    "media_type": ref.media_type,
                }
            ]
        if row["state"] in {"succeeded", "failed"}:
            if row["capability"] == "ocr":
                # Preserve the OCR v1 receipt contract exactly.
                result["actual"] = {"cost_usd": 0, "gpu_minutes": 0}
            elif row["actual_cost"] is not None and row["actual_gpu"] is not None:
                result["actual"] = {"cost_usd": row["actual_cost"], "gpu_minutes": row["actual_gpu"]}
            else:
                # A SAM terminal state without measured GPU usage is not a
                # zero-cost settlement; the coordinator must reconcile it.
                result["actual"] = None
        else:
            result["actual"] = None
        return result

    def accept(self, job: OCRJob | SAMJob):
        job = type(job).model_validate(job.model_dump(mode="json"))
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
            db.execute(
                "UPDATE jobs SET capability=? WHERE operation_id=?", (job.capability, job.operation_id)
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

    def finish(
        self,
        request_id: str,
        *,
        data: bytes | None = None,
        role: str = "texts",
        error_code: str | None = None,
        actual: tuple[float, float] | None = None,
    ):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE request_id=?", (request_id,)).fetchone()
            if not row or row["state"] not in {"running", "cancel_requested"}:
                raise PipelineError("operation_state")
            ref = self.outputs.put(row["task_id"], request_id, data, role=role) if data is not None else None
            db.execute(
                "UPDATE jobs SET state=?,output_ref=?,error_code=?,actual_cost=?,actual_gpu=? WHERE request_id=?",
                (
                    "failed" if error_code else "succeeded",
                    canonical_json(ref) if ref else None,
                    error_code,
                    actual[0] if actual else None,
                    actual[1] if actual else None,
                    request_id,
                ),
            )

    def output(self, request_id: str, output_id: str, *, task_id: str) -> bytes:
        self.job(request_id, task_id=task_id)
        with self.transaction() as db:
            row = db.execute("SELECT output_ref FROM jobs WHERE request_id=?", (request_id,)).fetchone()
        if not row or not row[0]:
            raise PipelineError("not_found")
        ref = ArtifactRef.model_validate_json(row[0])
        if output_id != ref.role:
            raise PipelineError("not_found")
        return self.outputs.read(ref)

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
    def __init__(self, root, artifacts, *, token, signing_key, engine, model_digest, capability="ocr"):
        if not token or len(signing_key) < 32:
            raise PipelineError("vision_not_ready", "Configure local authentication and signing material")
        self.jobs = VisionJobs(root)
        self.artifacts, self.token, self.signing_key = artifacts, token, signing_key
        if capability not in {"ocr", "segmentation"}:
            raise PipelineError("invalid_capability")
        self.engine, self.model_digest, self.capability = engine, model_digest, capability
        self._lock = threading.Lock()

    def dispatch(self, method, path, headers, body):
        if not hmac.compare_digest(headers.get("Authorization", ""), "Bearer " + self.token):
            return 401, {"error_code": "authentication_required"}
        try:
            if method == "GET" and path == "/v1/health":
                loader_ready = self.engine is not None and (
                    self.capability == "ocr"
                    or getattr(self.engine, "loader_ready", getattr(self.engine, "execution_ready", False))
                )
                execution_ready = self.engine is not None and (
                    self.capability == "ocr" or getattr(self.engine, "execution_ready", False)
                )
                health = {
                    "protocol_version": 1,
                    "capability": self.capability,
                    "device": "cpu" if self.capability == "ocr" else "cuda",
                    "model_digest": self.model_digest,
                    "ready": execution_ready,
                    "reason": (
                        None
                        if execution_ready
                        else "model_not_ready" if not loader_ready else "segmentation_not_ready"
                    ),
                }
                if self.capability == "segmentation":
                    health["loader_ready"] = loader_ready
                    release_metrics = getattr(self.engine, "release_metrics", None)
                    if isinstance(release_metrics, dict):
                        health["release_metrics"] = release_metrics
                return 200, health
            task_id = (
                None
                if path == "/v1/models/release"
                else TypeAdapter(Identifier).validate_python(headers.get("X-Task-ID", ""))
            )
            parts = path.strip("/").split("/")
            if method == "POST" and path == "/v1/jobs":
                job = OCRJob.model_validate_json(body) if self.capability == "ocr" else SAMJob.model_validate_json(body)
                if task_id != job.task_id:
                    raise PipelineError("operation_scope")
                if job.capability != self.capability:
                    raise PipelineError("prohibited_capability")
                permit = (
                    verify_permit(headers.get("X-Execution-Permit", ""), job, self.signing_key)
                    if self.capability == "ocr"
                    else verify_sam_permit(headers.get("X-Execution-Permit", ""), job, self.signing_key)
                )
                if self.engine is None or job.model_digest != self.model_digest:
                    raise PipelineError("model_not_ready")
                if self.capability == "segmentation" and not getattr(self.engine, "execution_ready", False):
                    raise PipelineError("segmentation_not_ready")
                for ref in (
                    (job.view_ref,)
                    if self.capability == "ocr"
                    else (job.canonical_ref, job.selection_ref, job.model_snapshot_ref)
                ):
                    self.artifacts.read(ref)
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
                    released_cuda_allocated_bytes = None
                    release_metrics = None
                    if self.engine:
                        try:
                            acknowledgement = self.engine.release()
                            released_cuda_allocated_bytes = getattr(
                                self.engine, "released_cuda_allocated_bytes", None
                            )
                            release_metrics = getattr(self.engine, "release_metrics", None)
                        except Exception:
                            # A provider-side release exception leaves GPU
                            # ownership uncertain; keep the engine reference
                            # and expose the stable boundary error.
                            acknowledgement = None
                        if self.capability == "segmentation" and acknowledgement is not True:
                            response = {"error_code": "resource_release_unknown"}
                            if isinstance(release_metrics, dict):
                                response["release_metrics"] = release_metrics
                            return 409, response
                        self.engine = None
                response = {
                    "released": True,
                    "device": "cpu" if self.capability == "ocr" else "cuda",
                }
                if released_cuda_allocated_bytes is not None:
                    response["cuda_allocated_bytes"] = released_cuda_allocated_bytes
                if isinstance(release_metrics, dict):
                    response["release_metrics"] = release_metrics
                return 200, response
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
            if self.capability == "ocr":
                value = self.engine.recognize(self.artifacts, job)
                data, role, actual = canonical_json(value).encode(), "texts", None
            else:
                started = time.perf_counter()
                startup_seconds = getattr(self.engine, "consume_startup_seconds", lambda: 0.0)()
                value = self.engine.segment(self.artifacts, job)
                if isinstance(value, bytes):
                    data, role = value, "segmentation"
                else:
                    data, role = canonical_json(value).encode(), "segmentation"
                actual = (
                    0.0,
                    max(0.0, (startup_seconds + time.perf_counter() - started) / 60.0),
                )
            if len(data) > 8 * 1024**2:
                raise PipelineError("output_limit")
            self.jobs.finish(request_id, data=data, role=role, actual=actual)
        except Exception:
            # Keep provider responses, paths and request bodies out of logs.
            error_code = "segmentation_failed" if self.capability == "segmentation" else "ocr_failed"
            LOGGER.error("Vision operation failed capability=%s code=%s", self.capability, error_code)
            self.jobs.finish(
                request_id,
                error_code=error_code,
            )


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
    from ..paths import find_repo_root
    from .ocr import PaddleOCREngine
    from .settings import load_ocr_settings

    parser = argparse.ArgumentParser()
    parser.add_argument("--capability", choices=["ocr", "segmentation"], required=True)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--validation-root",
        help="Relative directory below .local/validation/ui-analysis for an isolated acceptance ledger",
    )
    args = parser.parse_args()
    root = find_repo_root()
    if args.capability == "ocr":
        config, lock = load_ocr_settings(root)
        configure_paddlex_cache(root, config["cache_root"])
    else:
        from .sam_loader import SAM2Engine, load_sam_settings

        config, lock = load_sam_settings(root)
    if args.validation_root:
        if args.capability != "segmentation":
            parser.error("Validation roots are supported only for segmentation acceptance")
        artifact_root, job_root = validation_runtime_paths(root, args.validation_root)
    else:
        artifact_root, job_root = root / config["artifact_root"], root / config["job_root"]
    token = get_setting(config["token_env"], "")
    key = get_setting(config["signing_key_env"], "")
    if not token or len(key) < 32:
        parser.error("Configure vision authentication and signing material before starting")
    try:
        engine = PaddleOCREngine(lock, root) if args.capability == "ocr" else SAM2Engine(lock, root)
    except PipelineError:
        engine = None
    application = VisionApplication(
        job_root,
        ArtifactStore(artifact_root),
        token=token,
        signing_key=key,
        engine=engine,
        model_digest=digest(lock),
        capability=args.capability,
    )
    serve(application, port=args.port)


if __name__ == "__main__":
    main()
