"""Frozen local OCR/VLM revision inputs, with no provider calls."""

from io import BytesIO
from typing import Literal

from PIL import Image
from pydantic import Field

from ..pipelines.errors import PipelineError
from ..schemas.pipeline import ArtifactRef, PipelineModel
from ..schemas.ui import ImageView, UIAnalysisRequest


class LocalRevisionInputs(PipelineModel):
    schema_version: Literal[1] = 1
    action: Literal["reread_text", "review_region"]
    analysis_request_ref: ArtifactRef
    revision_request_ref: ArtifactRef
    selection_ref: ArtifactRef
    selection_revision: int = Field(ge=0, strict=True)
    view_ref: ArtifactRef
    texts_ref: ArtifactRef
    parameters_ref: ArtifactRef


def validate_local_request(store, value, *, task_id, purpose, selection_ref, selection_revision):
    from .revision import RevisionRequest

    try:
        request = LocalRevisionInputs.model_validate(value)
        if request.schema_version != 1 or request.action != purpose:
            raise ValueError("action")
        refs = {
            "analysis_request_ref": "request", "revision_request_ref": "revision_request",
            "selection_ref": "selection", "view_ref": "view_manifest",
            "texts_ref": "texts", "parameters_ref": "parameters",
        }
        for name, role in refs.items():
            ref = getattr(request, name)
            if ref.task_id != task_id or ref.role != role:
                raise PipelineError("artifact_scope")
            store.read(ref)
        if request.selection_ref != selection_ref or request.selection_revision != selection_revision:
            raise PipelineError("selection_conflict")
        revision = RevisionRequest.model_validate_json(store.read(request.revision_request_ref))
        if revision.action != purpose:
            raise PipelineError("input_changed")
        context = UIAnalysisRequest.model_validate_json(store.read(request.analysis_request_ref))
        if not context.allow_local_revision or context.selection_ref != selection_ref:
            raise PipelineError("revision_not_allowed")
        for ref in context.references():
            if ref.task_id != task_id:
                raise PipelineError("artifact_scope")
            store.read(ref)
        view = ImageView.model_validate_json(store.read(request.view_ref))
        if view.kind != "local" or view.canonical_ref != context.model_bindings.get("canonical_ref"):
            raise PipelineError("input_changed")
        for ref in (view.input_ref, view.canonical_ref):
            if ref.task_id != task_id:
                raise PipelineError("artifact_scope")
            store.read(ref)
        # Local revisions use an unscaled canonical crop. A valid inverse
        # matrix alone would also permit translating an unrelated input image.
        x1, y1, x2, y2 = view.crop
        forward = ((1.0, 0.0, -float(x1)), (0.0, 1.0, -float(y1)), (0.0, 0.0, 1.0))
        if view.forward != forward or (view.width, view.height) != (x2 - x1, y2 - y1):
            raise PipelineError("input_changed")
        if max(view.width, view.height) > context.resources.vlm_longest_edge:
            raise PipelineError("resource_insufficient")
        with Image.open(BytesIO(store.read(view.canonical_ref))) as canonical:
            with Image.open(BytesIO(store.read(view.input_ref))) as local:
                if x2 > canonical.width or y2 > canonical.height or local.size != (view.width, view.height):
                    raise PipelineError("input_changed")
                expected = canonical.crop(view.crop).convert("RGBA")
                if local.convert("RGBA").tobytes() != expected.tobytes():
                    raise PipelineError("input_changed")
        return request, context
    except PipelineError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise PipelineError("invalid_child") from exc


def analysis_context(store, plan):
    """Reuse the existing model adapters without changing old request bytes."""
    ref = ArtifactRef.model_validate(plan.parameters["request_ref"])
    if plan.workflow_type in {"ui_text_revision", "ui_region_revision"}:
        request = LocalRevisionInputs.model_validate_json(store.read(ref))
        _, context = validate_local_request(
            store, request, task_id=plan.task_id, purpose=plan.parameters["purpose"],
            selection_ref=ArtifactRef.model_validate(plan.parameters["selection_ref"]),
            selection_revision=plan.parameters["selection_revision"],
        )
        if context.budget != plan.envelope.budget:
            raise PipelineError("budget_scope")
        return context
    return UIAnalysisRequest.model_validate_json(store.read(ref))
