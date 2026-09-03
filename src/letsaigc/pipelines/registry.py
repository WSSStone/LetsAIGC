"""Static registrations: no dynamic imports or shell-like runtime tools."""

from ..schemas.pipeline import PipelinePlan
from .contracts import Capability
from .errors import PipelineError

CAPABILITIES = {
    "simulation.generate": Capability(id="simulation.generate", idempotent_submission=True, can_cancel=True),
    "comfy.generate": Capability(id="comfy.generate", can_cancel=True, resource="local-gpu"),
}
WORKFLOWS = {
    "temporal_smoke": ("letsaigc.smoke.v1", "simulation.generate"),
    "comfy_generation": ("letsaigc.comfy.v1", "comfy.generate"),
}


def validate_registration(plan: PipelinePlan) -> Capability:
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
