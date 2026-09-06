import base64
import hashlib
import hmac
import json
import time
from typing import Literal

from pydantic import Field, model_validator

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, Digest, Identifier, PipelineModel, canonical_json, digest
from ..schemas.ui import UIAnalysisRequest, UIResourceLimits, UISegmentationPrompt, UISegmentationRequest


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


class SAMJob(PipelineModel):
    """A frozen segmentation submission understood by the isolated service.

    It carries only references and bounded prompts.  The coordinator is still
    responsible for proving that these values equal the approved child request
    before issuing a permit.
    """

    schema_version: Literal[1] = 1
    task_id: Identifier
    operation_id: Identifier
    capability: Literal["segmentation"] = "segmentation"
    canonical_ref: ArtifactRef
    selection_ref: ArtifactRef
    selection_revision: int = Field(ge=0, strict=True)
    selection_hash: Digest
    model_snapshot_ref: ArtifactRef
    model_digest: Digest
    prompts: list[UISegmentationPrompt] = Field(min_length=1, max_length=64)
    parameters_hash: Digest
    resources: UIResourceLimits = Field(default_factory=UIResourceLimits)

    @model_validator(mode="after")
    def valid_scope(self):
        if self.canonical_ref.role != "canonical":
            raise ValueError("SAM requires a canonical image")
        if self.selection_ref.role != "selection":
            raise ValueError("SAM requires a selection artifact")
        if self.model_snapshot_ref.role != "model_snapshot":
            raise ValueError("SAM requires a model snapshot")
        if self.model_digest != self.model_snapshot_ref.sha256:
            raise ValueError("SAM model digest does not match model snapshot")
        if self.selection_hash != self.selection_ref.sha256:
            raise ValueError("SAM selection hash does not match selection_ref")
        refs = (self.canonical_ref, self.selection_ref, self.model_snapshot_ref)
        if any(ref.task_id != self.task_id for ref in refs):
            raise ValueError("SAM references must share task scope")
        return self


class VisionPermit(PipelineModel):
    schema_version: Literal[1] = 1
    task_id: Identifier
    operation_id: Identifier
    payload_hash: Digest
    expires_at: int = Field(gt=0, strict=True)
    resources: UIResourceLimits


class SAMPermit(PipelineModel):
    schema_version: Literal[1] = 1
    capability: Literal["segmentation"] = "segmentation"
    task_id: Identifier
    operation_id: Identifier
    payload_hash: Digest
    selection_hash: Digest
    model_digest: Digest
    parameters_hash: Digest
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


def issue_sam_permit(ledger, artifacts, job: SAMJob, signing_key: str) -> str:
    """Sign a child-approved, selection/model/input exact segmentation job."""

    operation = ledger.get(job.operation_id)
    binding = ledger.ui_binding(job.operation_id)
    plan = ledger.plan(job.task_id)
    if (
        operation.state != "submitting"
        or operation.task_id != job.task_id
        or plan.workflow_type != "ui_segmentation"
        or plan.envelope.allowed_capabilities != ["ui.segment"]
        or binding.capability != "ui.segment"
    ):
        raise PipelineError("approval_required")
    # The request is an immutable child artifact.  Re-validate it here so a
    # caller cannot construct a plausible SAMJob from a stale binding alone.
    try:
        request_ref = ArtifactRef.model_validate(plan.parameters.get("request_ref"))
    except Exception:
        raise PipelineError("input_changed") from None
    if (
        binding.selection_ref != job.selection_ref
        or binding.selection_revision != job.selection_revision
        or binding.selection_hash != job.selection_hash
        or request_ref not in binding.inputs
        or binding.dependency_hashes.get("sam-model") != job.model_digest
        or binding.dependency_hashes.get("sam-parameters") != job.parameters_hash
    ):
        raise PipelineError("input_changed")
    if job.model_digest != job.model_snapshot_ref.sha256:
        raise PipelineError("input_changed")
    try:
        request = UISegmentationRequest.model_validate_json(artifacts.read(request_ref))
    except Exception:
        raise PipelineError("input_changed") from None
    if request.canonical_ref.task_id != job.task_id:
        raise PipelineError("artifact_scope")
    if (
        request.canonical_ref != job.canonical_ref
        or request.selection_ref != job.selection_ref
        or request.selection_revision != job.selection_revision
        or request.selection_hash != job.selection_hash
        or request.model_snapshot_ref != job.model_snapshot_ref
        or request.prompts != job.prompts
        or request.resources != job.resources
        or digest(
            {
                "prompt_version": request.prompt_version,
                "prompts": [prompt.model_dump(mode="json") for prompt in request.prompts],
            }
        )
        != job.parameters_hash
    ):
        raise PipelineError("input_changed")
    for ref in (job.canonical_ref, job.selection_ref, job.model_snapshot_ref):
        artifacts.read(ref)
    if len(signing_key) < 32:
        raise PipelineError("vision_not_ready", "Vision signing key requires at least 32 characters")
    with ledger.transaction() as db:
        row = db.execute("SELECT approved,cancelled FROM tasks WHERE task_id=?", (job.task_id,)).fetchone()
        if not row or not row["approved"] or row["cancelled"]:
            raise PipelineError("approval_required")
        owner = db.execute(
            "SELECT operation_id FROM resources WHERE resource='local-gpu'"
        ).fetchone()
        if not owner or owner["operation_id"] != job.operation_id:
            raise PipelineError("resource_scope")
        child = db.execute(
            "SELECT status,active,selection_hash FROM ui_child_bindings WHERE task_id=?", (job.task_id,)
        ).fetchone()
        if (
            not child
            or child["status"] != "active"
            or not child["active"]
            or child["selection_hash"] != job.selection_hash
        ):
            raise PipelineError("selection_superseded")
        gate = db.execute(
            "SELECT g.submission_gate,t.cancelled FROM ui_budget_groups g JOIN tasks t ON t.task_id=g.root_task_id "
            "WHERE g.root_task_id=(SELECT root_task_id FROM ui_child_bindings WHERE task_id=?)",
            (job.task_id,),
        ).fetchone()
        if not gate or gate["submission_gate"] != "open" or gate["cancelled"]:
            raise PipelineError("cancelled")
    permit = SAMPermit(
        task_id=job.task_id,
        operation_id=job.operation_id,
        payload_hash=digest(job),
        selection_hash=job.selection_hash,
        model_digest=job.model_digest,
        parameters_hash=job.parameters_hash,
        expires_at=int(time.time()) + 60,
        resources=job.resources,
    )
    body = base64.urlsafe_b64encode(canonical_json(permit).encode()).decode()
    signature = hmac.new(signing_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    return body + "." + signature


def verify_sam_permit(value: str, job: SAMJob, signing_key: str) -> SAMPermit:
    try:
        if len(value) > 8192 or len(signing_key) < 32:
            raise ValueError("Invalid permit")
        body, signature = value.split(".")
        expected = hmac.new(signing_key.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid signature")
        permit = SAMPermit.model_validate(json.loads(base64.urlsafe_b64decode(body)))
        if permit.expires_at < time.time() or permit.expires_at > time.time() + 120:
            raise ValueError("Expired permit")
        if (
            permit.task_id,
            permit.operation_id,
            permit.payload_hash,
            permit.selection_hash,
            permit.model_digest,
            permit.parameters_hash,
        ) != (
            job.task_id,
            job.operation_id,
            digest(job),
            job.selection_hash,
            job.model_digest,
            job.parameters_hash,
        ):
            raise ValueError("Wrong permit scope")
        return permit
    except Exception:
        raise PipelineError("invalid_permit", "A valid operation-specific segmentation permit is required") from None
