from ..pipelines.errors import PipelineError
from ..schemas.pipeline import canonical_json
from ..schemas.ui import UIResourceLimits
from ..schemas.ui_provider import ManualUIInput, UIProvisionResult
from ..ui_analysis.normalize import validate_ui_image
from .base import ProvisionContext
from .intake import frozen_manifest


class ManualUIProvider:
    id = "manual"
    version = "1"

    def provide(self, request: ManualUIInput, context: ProvisionContext) -> UIProvisionResult:
        if "ui.manual" not in context.allowed_capabilities:
            raise PipelineError("approval_required")
        request = ManualUIInput.model_validate(request.model_dump(mode="json"))
        if any(ref.task_id != context.task_id for ref in request.inputs):
            raise PipelineError("artifact_scope")
        manifest = frozen_manifest(context.artifacts, request, context.task_id)
        for source in manifest.sources:
            validate_ui_image(
                context.artifacts.read(source.original_ref), source.original_ref.media_type, UIResourceLimits()
            )
        errors = [entry.model_dump(mode="json") for entry in manifest.entries if entry.status == "failed"]
        error_ref = (
            context.artifacts.put(
                context.task_id, context.operation_id, canonical_json(errors).encode(), role="input_errors"
            )
            if errors
            else None
        )
        return UIProvisionResult(
            provider_id=self.id,
            provider_version=self.version,
            status=manifest.status,
            sources=manifest.sources,
            index_ref=request.metadata_ref,
            errors_ref=error_ref,
        )
