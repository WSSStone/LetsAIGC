import base64
import hashlib
import hmac
import json
import time
from typing import Literal

from pydantic import Field, model_validator

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, Digest, Identifier, PipelineModel, canonical_json, digest
from ..schemas.ui import UIAnalysisRequest, UIResourceLimits


class OCRJob(PipelineModel):
    schema_version: Literal[1] = 1
    task_id: Identifier
    operation_id: Identifier
    capability: Literal["ocr"] = "ocr"
    view_ref: ArtifactRef
    model_digest: Digest
    language: Literal["auto", "zh", "en"] = "auto"
    text_id: Identifier | None = None

    @model_validator(mode="after")
    def view_scope(self):
        if self.view_ref.task_id != self.task_id or self.view_ref.role != "view_manifest":
            raise ValueError("OCR requires a task-scoped view description")
        return self


class VisionPermit(PipelineModel):
    schema_version: Literal[1] = 1
    task_id: Identifier
    operation_id: Identifier
    payload_hash: Digest
    expires_at: int = Field(gt=0, strict=True)
    resources: UIResourceLimits


def issue_permit(ledger, artifacts, job: OCRJob, signing_key: str) -> str:
    """Only the trusted coordinator can sign an already admitted operation."""
    operation = ledger.get(job.operation_id)
    binding = ledger.ui_binding(job.operation_id)
    plan = ledger.plan(job.task_id)
    request = UIAnalysisRequest.model_validate_json(
        artifacts.read(ArtifactRef.model_validate(plan.parameters["request_ref"]))
    )
    if operation.state != "submitting" or operation.task_id != job.task_id or binding.capability != "ui.ocr":
        raise PipelineError("approval_required")
    if job.view_ref not in binding.inputs or binding.dependency_hashes.get("ocr-model") != job.model_digest:
        raise PipelineError("input_changed")
    model = request.model_bindings.get("ocr")
    if model is None or model.sha256 != job.model_digest or request.language != job.language:
        raise PipelineError("input_changed")
    artifacts.read(model)
    with ledger.transaction() as db:
        row = db.execute("SELECT approved,cancelled FROM tasks WHERE task_id=?", (job.task_id,)).fetchone()
        if not row or not row["approved"] or row["cancelled"]:
            raise PipelineError("approval_required")
    if len(signing_key) < 32:
        raise PipelineError("vision_not_ready", "Vision signing key requires at least 32 characters")
    permit = VisionPermit(
        task_id=job.task_id,
        operation_id=job.operation_id,
        payload_hash=digest(job),
        expires_at=int(time.time()) + 60,
        resources=request.resources,
    )
    body = base64.urlsafe_b64encode(canonical_json(permit).encode()).decode()
    signature = hmac.new(signing_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    return body + "." + signature


def verify_permit(value: str, job: OCRJob, signing_key: str) -> VisionPermit:
    try:
        if len(value) > 8192 or len(signing_key) < 32:
            raise ValueError("Invalid permit")
        body, signature = value.split(".")
        expected = hmac.new(signing_key.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid signature")
        permit = VisionPermit.model_validate(json.loads(base64.urlsafe_b64decode(body)))
        if permit.expires_at < time.time() or permit.expires_at > time.time() + 120:
            raise ValueError("Expired permit")
        if (permit.task_id, permit.operation_id, permit.payload_hash) != (job.task_id, job.operation_id, digest(job)):
            raise ValueError("Wrong permit scope")
        return permit
    except Exception:
        raise PipelineError("invalid_permit", "A valid operation-specific execution permit is required") from None
