from __future__ import annotations

from pathlib import Path
from typing import Protocol

from PIL import Image, UnidentifiedImageError

from ..errors import ValidationError
from ..media import create_contact_sheet, probe_media
from ..paths import local_path
from ..schemas import AgentEvaluation, GenerationPlan


class CriticModel(Protocol):
    def evaluate(self, intent: str, output_paths: list[Path], hard_constraints: dict[str, bool]) -> AgentEvaluation: ...


class AgentCritic:
    def __init__(self, model: CriticModel) -> None:
        self.model = model

    def evaluate(self, plan: GenerationPlan, output_paths: list[Path], *, iteration_id: str) -> AgentEvaluation:
        hard = {
            "output_count": len(output_paths) == plan.envelope.output_count,
            "files_exist": bool(output_paths) and all(path.is_file() for path in output_paths),
            "dimensions": True,
            "decodable": True,
        }
        visual_paths: list[Path] = []
        for path in output_paths:
            if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}:
                try:
                    media = probe_media(path)
                    hard["dimensions"] &= bool(
                        media.width
                        and media.height
                        and media.width <= plan.envelope.max_width
                        and media.height <= plan.envelope.max_height
                    )
                    if plan.parameters.get("fps") is not None:
                        hard["fps"] = media.fps is not None and abs(media.fps - plan.parameters["fps"]) < 0.01
                    if plan.parameters.get("frames") is not None:
                        hard["frames"] = media.frame_count == plan.parameters["frames"]
                    evidence = create_contact_sheet(
                        path,
                        local_path("agent", "tasks", plan.task_id, "iterations", iteration_id, "video-evidence"),
                    )
                    hard["black_frames"] = evidence["black_frames_passed"]
                    visual_paths.append(Path(evidence["contact_sheet"]))
                except Exception:
                    hard["decodable"] = False
            else:
                try:
                    with Image.open(path) as image:
                        image.verify()
                    with Image.open(path) as image:
                        width, height = image.size
                        if plan.envelope.background == "transparent":
                            hard["alpha"] = "A" in image.getbands() and image.getchannel("A").getextrema()[0] < 255
                    hard["dimensions"] &= width <= plan.envelope.max_width and height <= plan.envelope.max_height
                    if "size" in plan.parameters:
                        required = tuple(int(part) for part in plan.parameters["size"].split("x"))
                        hard["requested_size"] = (width, height) == required
                    elif "width" in plan.parameters and "height" in plan.parameters:
                        hard["requested_size"] = (width, height) == (
                            plan.parameters["width"], plan.parameters["height"]
                        )
                    visual_paths.append(path)
                except (OSError, UnidentifiedImageError):
                    hard["decodable"] = False
        if not visual_paths:
            return AgentEvaluation(hard_constraints=hard, score=0, issues=["No decodable visual candidate"])
        instructions = (
            f"{plan.user_intent}\nAcceptance criteria: {plan.acceptance_criteria}\n"
            f"Current parameters: {plan.parameters}\n"
            f"Deterministic media checks: {hard}\n"
            f"Only propose changes to: {plan.envelope.mutable_parameters}"
        )
        evaluation = self.model.evaluate(instructions, visual_paths, dict(hard))
        # The model scores intent; it cannot override deterministic safety checks.
        evaluation.hard_constraints = {**evaluation.hard_constraints, **hard}
        return evaluation


def validate_revision(plan: GenerationPlan, revision: dict) -> dict:
    allowed = set(plan.envelope.mutable_parameters)
    unknown = sorted(set(revision) - allowed)
    if unknown:
        raise ValidationError(f"Critic attempted to modify non-approved parameters: {', '.join(unknown)}")
    updated = dict(plan.parameters)
    updated.update(revision)
    return updated
