import hashlib
import json
from urllib.parse import urlsplit

import httpx
from pydantic import TypeAdapter

from ..pipelines.contracts import Capability, Submission
from ..pipelines.errors import PipelineError
from ..schemas.pipeline import Cost, Identifier
from ..schemas.ui import UIObservation, UIStepBinding
from .base import OCRJob, issue_permit


class VisionClient:
    def __init__(self, *, token: str, endpoint="http://127.0.0.1:8766", transport=None):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.path not in {"", "/"}
            or (parsed.username or parsed.password or parsed.query or parsed.fragment)
            or not token
        ):
            raise PipelineError("vision_not_ready", "Vision requires a loopback endpoint and local authentication")
        self.client = httpx.Client(
            base_url=endpoint,
            headers={"Authorization": "Bearer " + token},
            timeout=30,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    def request(self, method, path, *, task_id=None, data=None, permit=None, output=False):
        headers = {}
        if task_id:
            headers["X-Task-ID"] = TypeAdapter(Identifier).validate_python(task_id)
        if permit:
            headers["X-Execution-Permit"] = permit
        try:
            with self.client.stream(method, path, headers=headers, json=data) as response:
                if response.status_code == 404:
                    return None
                if response.status_code not in {200, 202}:
                    raise ValueError("Vision request rejected")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > (8 * 1024**2 if output else 65536):
                        raise ValueError("Vision response too large")
                return bytes(body) if output else json.loads(body)
        except Exception:
            raise PipelineError(
                "vision_unavailable", "The local vision service could not confirm the request"
            ) from None

    def operation(self, operation_id, *, task_id):
        identity = TypeAdapter(Identifier).validate_python(operation_id)
        return self.request("GET", "/v1/operations/" + identity, task_id=task_id)

    def health(self):
        return self.request("GET", "/v1/health")


class OCROperationBackend:
    capability = Capability(id="ui.ocr", can_cancel=True)

    def __init__(self, ledger, artifacts, client: VisionClient, *, signing_key: str, model_digest: str):
        self.ledger, self.artifacts, self.client = ledger, artifacts, client
        self.signing_key, self.model_digest = signing_key, model_digest

    def submit(self, operation_id, arguments):
        binding = UIStepBinding.model_validate(arguments["binding"])
        refs = [ref for ref in binding.inputs if ref.role == "view_manifest"]
        if len(refs) != 1:
            raise PipelineError("invalid_input")
        params = json.loads(self.artifacts.read(binding.parameters_ref)) if binding.parameters_ref else {}
        job = OCRJob(
            task_id=binding.task_id,
            operation_id=operation_id,
            view_ref=refs[0],
            model_digest=self.model_digest,
            language=params.get("language", "auto"),
            text_id=params.get("text_id"),
        )
        permit = issue_permit(self.ledger, self.artifacts, job, self.signing_key)
        receipt = self.client.request(
            "POST", "/v1/jobs", task_id=job.task_id, data=job.model_dump(mode="json"), permit=permit
        )
        return Submission(request_id=receipt["request_id"], metadata={"task_id": job.task_id})

    def recover(self, operation_id):
        operation = self.ledger.get(operation_id)
        receipt = self.client.operation(operation_id, task_id=operation.task_id)
        if receipt is None:
            return None
        return Submission(request_id=receipt["request_id"], metadata={"task_id": operation.task_id})

    def inspect(self, submission):
        request_id = TypeAdapter(Identifier).validate_python(submission.request_id)
        job = self.client.request("GET", "/v1/jobs/" + request_id, task_id=submission.metadata["task_id"])
        if job is None or job["state"] == "unknown":
            return UIObservation(state="unknown", actual=None)
        terminal = job["state"] in {"succeeded", "failed"}
        return UIObservation(
            state=job["state"] if terminal else "running",
            actual=Cost.model_validate(job["actual"]) if terminal and job.get("actual") else None,
        )

    def collect(self, submission):
        identity = TypeAdapter(Identifier).validate_python(submission.request_id)
        task_id = submission.metadata["task_id"]
        result = self.client.request("GET", "/v1/jobs/" + identity, task_id=task_id)
        if result is None or result["state"] != "succeeded" or len(result["outputs"]) != 1:
            raise PipelineError("invalid_output")
        description = result["outputs"][0]
        if description["output_id"] != "texts":
            raise PipelineError("invalid_output")
        data = self.client.request("GET", "/v1/jobs/" + identity + "/outputs/texts", task_id=task_id, output=True)
        if (
            data is None
            or len(data) != description["size_bytes"]
            or hashlib.sha256(data).hexdigest() != description["sha256"]
        ):
            raise PipelineError("artifact_changed")
        return [("texts", data, "application/json")]

    def cancel(self, submission):
        identity = TypeAdapter(Identifier).validate_python(submission.request_id)
        self.client.request("POST", "/v1/jobs/" + identity + "/cancel", task_id=submission.metadata["task_id"])
        return self.inspect(submission)
