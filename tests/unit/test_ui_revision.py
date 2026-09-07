"""Offline T029 revision DTO, dependency closure, and reuse projection tests."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.pipeline import ArtifactRef
from letsaigc.ui_analysis.revision import (
    DEFAULT_DEPENDENCY_GRAPH,
    RevisionArtifact,
    RevisionRequest,
    dependency_closure,
    project_revision,
)

BASE = {
    "base_task_id": "ui-root",
    "base_fingerprint": "a" * 64,
    "base_revision": 1,
}


def request(action, target_ids=("element-1",), parameters=None):
    return RevisionRequest(
        **BASE,
        action=action,
        target_ids=list(target_ids),
        parameters={} if parameters is None else parameters,
    )


def ref(node: str, artifact_id: str, task_id: str = "ui-root") -> RevisionArtifact:
    return RevisionArtifact(
        node=node,
        artifact_ref=ArtifactRef(
            task_id=task_id,
            artifact_id=artifact_id,
            key=f"{task_id}/{artifact_id}.json",
            sha256=hashlib.sha256(artifact_id.encode()).hexdigest(),
            size_bytes=1,
            media_type="application/json",
            role=node,
            operation_id=f"op-{artifact_id}",
        ),
        target_ids=[] if node == "manifest" else (["element-1"] if artifact_id.endswith("1") else ["element-2"]),
    )


def test_all_four_actions_have_bounded_parameter_contracts():
    assert request("reread_text", ("text-1",)).parameters == {}
    assert request("review_region", parameters={"user_notes": "check overlap"}).action == "review_region"
    segment = request(
        "adjust_segmentation",
        parameters={"prompts": [{"element_id": "element-1", "box": [0, 0, 32, 32], "points": [[4, 4]]}]},
    )
    assert segment.parameters["prompts"][0]["element_id"] == "element-1"
    regenerate = request(
        "regenerate",
        parameters={"prompt": "restore the panel", "negative_prompt": "text", "seed": 7},
    )
    assert regenerate.parameters["seed"] == 7


@pytest.mark.parametrize(
    ("action", "parameters"),
    [
        ("reread_text", {"user_notes": "not allowed here"}),
        ("review_region", {"region": [0, 0, 10, 10]}),
        ("adjust_segmentation", {"prompts": [{"element_id": "element-2", "box": [0, 0, 4, 4]}]}),
        ("regenerate", {"steps": 30}),
        ("regenerate", {"model": "other-model"}),
        ("regenerate", {"denoise": 0.5}),
    ],
)
def test_action_parameters_cannot_expand_the_approved_plan(action, parameters):
    with pytest.raises((ValidationError, PipelineError)):
        request(action, parameters=parameters)


def test_request_rejects_unknown_action_duplicate_ids_and_bad_bounds():
    with pytest.raises(ValidationError):
        request("not-an-action")
    with pytest.raises(ValidationError):
        request("regenerate", ("element-1", "element-1"))
    with pytest.raises((ValidationError, PipelineError)):
        request(
            "adjust_segmentation",
            parameters={"prompts": [{"element_id": "element-1", "box": [0, 0, 9000, 32]}]},
        )


def test_dependency_closure_propagates_text_and_region_changes():
    text = request("reread_text", ("text-1",))
    closure = dependency_closure(text, known_target_ids={"text-1"})
    assert closure.roots == ["ocr"]
    assert closure.nodes == [
        "ocr", "analyze", "layout", "crop", "segmentation", "glyphs", "mask", "inpaint", "manifest"
    ]
    assert closure.affected_target_ids == ["text-1"]

    segment = request(
        "adjust_segmentation",
        parameters={"prompts": [{"element_id": "element-1", "box": [0, 0, 32, 32]}]},
    )
    assert dependency_closure(segment, known_target_ids={"element-1"}).nodes == [
        "segmentation",
        "glyphs",
        "mask",
        "inpaint",
        "manifest",
    ]


def test_projection_reuses_unaffected_ids_and_keeps_suggestions_non_destructive():
    artifacts = [
        ref("layout", "layout-1"),
        ref("crop", "crop-1"),
        ref("crop", "crop-2"),
        ref("segmentation", "segment-1"),
        ref("inpaint", "inpaint-2"),
        ref("manifest", "manifest-0"),
    ]
    revision = request("review_region", ("element-1",), {"user_notes": "review panel"})
    projection = project_revision(revision, artifacts)
    assert projection.proposal_only is True
    assert {item.artifact_ref.artifact_id for item in projection.recompute} == {
        "layout-1", "crop-1", "segment-1", "manifest-0"
    }
    assert {item.artifact_ref.artifact_id for item in projection.reuse} == {"crop-2", "inpaint-2"}
    assert projection.effective_reuse == artifacts
    assert projection.recompute_requires_adoption is True


def test_projection_adjustment_invalidates_only_target_chain():
    artifacts = [ref("crop", "crop-1"), ref("crop", "crop-2"), ref("inpaint", "inpaint-1"), ref("inpaint", "inpaint-2")]
    revision = request(
        "adjust_segmentation",
        parameters={"prompts": [{"element_id": "element-1", "box": [0, 0, 32, 32]}]},
    )
    projection = project_revision(revision, artifacts)
    assert projection.new_plan_required is True
    assert projection.approval_required is True
    assert {item.artifact_ref.artifact_id for item in projection.recompute} == {"inpaint-1"}
    assert {item.artifact_ref.artifact_id for item in projection.reuse} == {"crop-1", "crop-2", "inpaint-2"}


def test_closure_rejects_unknown_targets_and_dependency_cycles():
    with pytest.raises(PipelineError) as error:
        dependency_closure(request("regenerate", ("missing",)), known_target_ids={"element-1"})
    assert error.value.code == "unknown_target"
    cyclic = {**DEFAULT_DEPENDENCY_GRAPH, "ocr": ("layout",)}
    with pytest.raises(PipelineError) as error:
        dependency_closure(request("reread_text"), graph=cyclic)
    assert error.value.code == "dependency_cycle"
