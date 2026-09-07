"""Static registrations: no dynamic imports or shell-like runtime tools."""

from ..schemas.pipeline import PipelinePlan
from .contracts import Capability
from .errors import PipelineError
from .ui_children import CHILD_WORKFLOWS, is_child_plan, validate_child_plan

CAPABILITIES = {
    "simulation.generate": Capability(id="simulation.generate", idempotent_submission=True, can_cancel=True),
    "comfy.generate": Capability(id="comfy.generate", can_cancel=True, resource="local-gpu"),
    # Registered for trusted child planning only.  T022+ provide the provider
    # adapters; leaving these without a backend prevents a legacy Comfy fallthrough.
    "ui.segment": Capability(id="ui.segment", resource="local-gpu"),
    "ui.inpaint": Capability(id="ui.inpaint", resource="local-gpu"),
}
WORKFLOWS = {
    "temporal_smoke": ("letsaigc.smoke.v1", "simulation.generate"),
    "comfy_generation": ("letsaigc.comfy.v1", "comfy.generate"),
}
for _purpose, (_workflow, _capability) in CHILD_WORKFLOWS.items():
    WORKFLOWS[_workflow] = (f"letsaigc.ui.{_purpose}.v1", _capability)


def validate_registration(plan: PipelinePlan) -> Capability:
    if plan.workflow_type == "ui_analysis":
        validate_ui_registration(plan)
        return UI_CAPABILITIES["ui.analyze"]
    if is_child_plan(plan):
        validate_child_plan(plan)
        return CAPABILITIES[plan.envelope.allowed_capabilities[0]]
    definition = WORKFLOWS.get(plan.workflow_type)
    if not definition or plan.envelope.allowed_capabilities != [definition[1]]:
        raise PipelineError("prohibited_capability", "Workflow and capability registration do not match")
    if plan.workflow_type == "comfy_generation":
        if plan.generation_plan is None or plan.parameters:
            raise PipelineError("invalid_plan", "Comfy pipeline requires an immutable generation plan reference")
        if plan.envelope.budget.max_revisions:
            raise PipelineError("invalid_plan", "Comfy v1 uses explicit new plans for generation revisions")
    else:
        if plan.generation_plan is not None or set(plan.parameters) - {"accept_after_revision"}:
            raise PipelineError("invalid_plan", "Unsupported simulation parameters")
        threshold = plan.parameters.get("accept_after_revision", 0)
        if type(threshold) is not int or not 0 <= threshold <= plan.envelope.budget.max_revisions:
            raise PipelineError("invalid_plan", "Simulation acceptance revision is outside the approved budget")
    return CAPABILITIES[definition[1]]


UI_CAPABILITIES = {
    "ui." + name: Capability(id="ui." + name)
    for name in ("manual", "search", "normalize", "ocr", "analyze", "layout", "crop", "project")
}
UI_OUTPUT_ROLES = {
    "ui.manual": {"sources", "input_manifest", "provenance"},
    "ui.search": {"sources", "input_manifest", "provenance"},
    "ui.normalize": {"canonical", "transforms", "canonical_index"},
    "ui.ocr": {"texts"},
    "ui.analyze": {"analysis"},
    "ui.layout": {"layout", "quality_report"},
    "ui.crop": {"rect_crop", "overlay", "asset_index"},
    "ui.project": {"manifest"},
    "ui.segment": {"segmentation"},
    "ui.inpaint": {"image", "reconstruction", "output"},
}
WORKFLOWS["ui_analysis"] = ("letsaigc.ui.analysis.v1", "ui.analyze")


def ui_capabilities(request) -> list[str]:
    return ["ui." + request.input.kind, "ui.normalize", "ui.ocr", "ui.analyze", "ui.layout", "ui.crop", "ui.project"]


def validate_ui_registration(plan: PipelinePlan, artifacts=None):
    from ..schemas.pipeline import ArtifactRef
    from ..schemas.ui import UIAnalysisRequest, UIEvaluationPolicy, UIPolicy

    if plan.workflow_type != "ui_analysis" or plan.envelope.stage != "analysis" or plan.generation_plan:
        raise PipelineError("invalid_plan", "UI preview requires an analysis plan")
    if plan.envelope.mutable_parameters:
        raise PipelineError("invalid_plan", "UI analysis inputs are immutable")
    if set(plan.parameters) != {"request_ref", "policy_ref", "evaluation_ref"}:
        raise PipelineError("invalid_plan", "UI plan only accepts the three registered policy references")
    if not plan.envelope.allowed_capabilities or set(plan.envelope.allowed_capabilities) - UI_CAPABILITIES.keys():
        raise PipelineError("prohibited_capability")
    refs = {key: ArtifactRef.model_validate(value) for key, value in plan.parameters.items()}
    if any(ref.task_id != plan.task_id or ref.role != key.removesuffix("_ref") for key, ref in refs.items()):
        raise PipelineError("artifact_scope", "UI policy references have incorrect roles or scope")
    if artifacts is None:
        return None
    request = UIAnalysisRequest.model_validate_json(artifacts.read(refs["request_ref"]))
    policy = UIPolicy.model_validate_json(artifacts.read(refs["policy_ref"]))
    UIEvaluationPolicy.model_validate_json(artifacts.read(refs["evaluation_ref"]))
    count = len(request.input.inputs) if request.input.kind == "manual" else request.input.max_images
    if count != 1:
        raise PipelineError("capability_not_ready", "UI batch execution is not available yet")
    if plan.envelope.allowed_capabilities != ui_capabilities(request) or plan.envelope.budget != request.budget:
        raise PipelineError("invalid_plan", "Analysis scope differs from the frozen request")
    if policy.resources != request.resources or policy.limits != request.limits:
        raise PipelineError("invalid_plan", "UI request and policy limits differ")
    for ref in request.references():
        if ref.task_id != plan.task_id:
            raise PipelineError("artifact_scope")
        artifacts.read(ref)
    return request


def resolve_ui_step(plan: PipelinePlan, binding):
    from ..schemas.ui import UIStepBinding

    binding = UIStepBinding.model_validate(binding.model_dump(mode="json"))
    child = is_child_plan(plan)
    if child:
        validate_child_plan(plan)
    else:
        validate_ui_registration(plan)
    if binding.task_id != plan.task_id or binding.capability not in plan.envelope.allowed_capabilities:
        raise PipelineError("prohibited_capability")
    name = binding.capability.removeprefix("ui.")
    if not child and binding.step_id != name and not binding.step_id.startswith(name + "."):
        raise PipelineError("invalid_step", "Step ID must belong to its registered capability")
    if child:
        return CAPABILITIES[binding.capability]
    return UI_CAPABILITIES[binding.capability]
