"""Resumable ComfyUI adapter. Submission itself has no server-side dedup guarantee."""

from __future__ import annotations

import time
import uuid
from urllib.parse import urlsplit

from pydantic import Field

from ..backends.comfy import ComfyBackend
from ..media import discover_declared_outputs, probe_media
from ..paths import local_path
from ..policy import verify_sha256
from ..schemas import GenerationPlan, MediaOutputDeclaration
from ..schemas.pipeline import Digest, Identifier, PipelineModel, digest
from ..tracking.manifest import add_output, load_manifest, save_manifest
from .contracts import Capability, Observation, Submission
from .errors import OutcomeUnknown, PipelineError


class ComfyReceipt(PipelineModel):
    run_id: Identifier
    task_id: Identifier
    operation_id: Identifier
    started_at: float = Field(ge=0)
    timeout_seconds: float = Field(gt=0)
    outputs: list[MediaOutputDeclaration]
    graph_sha256: Digest


class ComfyOperationBackend:
    capability = Capability(id="comfy.generate", can_cancel=True, resource="local-gpu")

    def __init__(self, backend: ComfyBackend | None = None) -> None:
        self.backend = backend or ComfyBackend()
        address = urlsplit(self.backend.client.base_url)
        if address.query or address.fragment or address.username or address.password:
            raise PipelineError("unsafe_endpoint", "ComfyUI endpoint cannot contain credentials or URL suffixes")

    def prepare(self, operation_id: str, arguments: dict) -> dict:
        plan = GenerationPlan.model_validate(arguments["generation_plan"])
        compiled, manifest, recipe = self.backend.prepare(plan, iteration_id=operation_id)
        metadata = {
            "run_id": manifest.run_id,
            "task_id": plan.task_id,
            "operation_id": operation_id,
            "started_at": time.time(),
            "timeout_seconds": min(
                recipe.resource_budget["timeout_seconds"], plan.envelope.budget.max_iteration_gpu_minutes * 60
            ),
            "outputs": compiled.outputs,
            "graph_sha256": compiled.graph_sha256,
        }
        ComfyReceipt.model_validate(metadata)
        manifest.parent_run_id = "pipeline-" + digest(plan.task_id)[:32]
        manifest.tracking["pipeline_operation_id"] = operation_id
        save_manifest(manifest)
        return {"graph": compiled.graph, "receipt": metadata}

    @staticmethod
    def checked_manifest(metadata: dict):
        receipt = ComfyReceipt.model_validate(metadata)
        manifest = load_manifest(receipt.run_id)
        if (
            manifest.parameters.get("agent_task_id") != receipt.task_id
            or manifest.parameters.get("iteration_id") != receipt.operation_id
            or manifest.source.get("compiled_graph_sha256") != receipt.graph_sha256
        ):
            raise PipelineError("receipt_mismatch", "Provider receipt does not match local operation provenance")
        return manifest

    def submit(self, operation_id: str, arguments: dict) -> Submission:
        prepared = arguments["prepared"]
        metadata = prepared["receipt"]
        manifest = self.checked_manifest(metadata)
        metadata["started_at"] = time.time()
        prompt_id = self.backend.client.submit(
            prepared["graph"],
            extra_data={"letsaigc_operation_id": operation_id, "letsaigc_receipt": metadata},
        )
        manifest.tracking["comfy_prompt_id"] = prompt_id
        manifest.status = "running"
        save_manifest(manifest)
        return Submission(request_id=prompt_id, metadata=metadata)

    def recover(self, operation_id: str) -> Submission | None:
        """Search surviving provider records; absence never proves non-submission."""
        matches: dict[str, Submission] = {}
        queue = self.backend.client.queue()
        prompts = list(queue.get("queue_running", [])) + list(queue.get("queue_pending", []))
        history = self.backend.client._get("/history")
        prompts.extend(record.get("prompt", []) for record in history.values() if isinstance(record, dict))
        for prompt in prompts:
            if not isinstance(prompt, list) or len(prompt) < 4 or not isinstance(prompt[3], dict):
                continue
            extra = prompt[3]
            if extra.get("letsaigc_operation_id") != operation_id:
                continue
            receipt = extra.get("letsaigc_receipt")
            if isinstance(receipt, dict):
                if receipt.get("operation_id") != operation_id:
                    raise OutcomeUnknown()
                self.checked_manifest(receipt)
                matches[str(prompt[1])] = Submission(request_id=str(prompt[1]), metadata=receipt)
        if len(matches) > 1:
            raise OutcomeUnknown()
        return next(iter(matches.values()), None)

    def inspect(self, submission: Submission) -> Observation:
        uuid.UUID(submission.request_id)
        ComfyReceipt.model_validate(submission.metadata)
        record = self.backend.client.history(submission.request_id).get(submission.request_id)
        elapsed = max(0, time.time() - submission.metadata["started_at"])
        if record is not None:
            status = record.get("status", {})
            state = (
                "failed"
                if status.get("status_str") == "error"
                else ("succeeded" if status.get("completed") is True or record.get("outputs") else "running")
            )
            if state == "running" and elapsed > submission.metadata["timeout_seconds"]:
                self.backend.client.cancel_owned_prompt(submission.request_id)
                return Observation(state="unknown")
            # Prefer provider execution timestamps; wall time is a conservative fallback.
            messages = dict(status.get("messages", []))
            start = messages.get("execution_start", {}).get("timestamp")
            end = (
                messages.get("execution_success")
                or messages.get("execution_error")
                or messages.get("execution_interrupted")
                or {}
            ).get("timestamp")
            duration = max(0, end - start) / 1000 if start is not None and end is not None else elapsed
            from ..schemas.pipeline import Cost

            return Observation(state=state, actual=Cost(gpu_minutes=duration / 60))
        queue = self.backend.client.queue()
        present = any(
            isinstance(item, list) and len(item) > 1 and item[1] == submission.request_id
            for item in [*queue.get("queue_running", []), *queue.get("queue_pending", [])]
        )
        if present and elapsed <= submission.metadata["timeout_seconds"]:
            return Observation(state="running")
        if present:
            self.backend.client.cancel_owned_prompt(submission.request_id)
        # Timeout/evicted history does not release cost or GPU ownership.
        return Observation(state="unknown")

    def collect(self, submission: Submission) -> list[tuple[str, bytes, str]]:
        record = self.backend.client.history(submission.request_id).get(submission.request_id)
        if record is None:
            raise OutcomeUnknown()
        declarations = [MediaOutputDeclaration.model_validate(item) for item in submission.metadata["outputs"]]
        outputs = discover_declared_outputs(record, declarations, local_path("output"))
        manifest = self.checked_manifest(submission.metadata)
        manifest.outputs = []
        result = []
        for index, output in enumerate(outputs):
            media = probe_media(output.path)
            add_output(manifest, output.path, role=output.role, media_kind=output.media_kind, media=media)
            verify_sha256(output.path, manifest.outputs[-1].sha256)
            result.append((f"{output.role}-{index}", output.path.read_bytes(), media.mime_type))
        if not result:
            raise PipelineError("missing_output", "ComfyUI completed without declared output")
        manifest.status = "succeeded"
        manifest.governance.validations["hashes"] = True
        save_manifest(manifest)
        return result

    def cancel(self, submission: Submission) -> Observation:
        self.backend.client.cancel_owned_prompt(submission.request_id)
        # A cancellation acknowledgment alone is not evidence of terminal completion.
        return self.inspect(submission)
