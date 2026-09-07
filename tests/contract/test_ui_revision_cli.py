"""CLI contracts for bounded T029 revision planning and acceptance."""

from __future__ import annotations

import json
from io import BytesIO

import pytest
from PIL import Image
from test_ui_editing_entrypoints import reviewed as reviewed_fixture
from typer.testing import CliRunner

from letsaigc.cli import app
from letsaigc.pipelines.migrations import migrate_ui_ledger
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef, canonical_json
from letsaigc.schemas.ui import UIAnalysisRequest
from letsaigc.schemas.ui_review import ReviewPatch
from letsaigc.ui_analysis import cli, runtime
from letsaigc.ui_analysis.revision_review import save_suggestion

BUDGET = {
    "max_total_cost_usd": 1,
    "max_iteration_cost_usd": 1,
    "max_total_gpu_minutes": 0,
    "max_iteration_gpu_minutes": 0,
    "max_revisions": 2,
}


def _png(size=(32, 24)):
    stream = BytesIO()
    Image.new("RGB", size, "#435466").save(stream, format="PNG")
    return stream.getvalue()


def _root_fixture(tmp_path, monkeypatch):
    service = PipelineService(tmp_path / "state", ui_schema=5)
    original = service.artifacts.put("root", "input", _png(), role="original", media_type="image/png")
    canonical = service.artifacts.put("root", "canonical", _png(), role="canonical", media_type="image/png")
    layout = service.artifacts.put(
        "root",
        "layout",
        canonical_json(
            {
                "schema_version": 1,
                "source_id": original.artifact_id,
                "width": 32,
                "height": 24,
                "elements": [{"element_id": "element-1", "kind": "text", "bbox": [2, 2, 18, 10]}],
            }
        ).encode(),
        role="layout",
    )
    texts = service.artifacts.put(
        "root",
        "texts",
        canonical_json(
            {
                "schema_version": 1,
                "canonical_sha256": canonical.sha256,
                "texts": [
                    {
                        "text_id": "text-1",
                        "text": "PLAY",
                        "score": 0.9,
                        "polygon": [[2, 2], [18, 2], [18, 10], [2, 10]],
                    }
                ],
            }
        ).encode(),
        role="texts",
    )
    selection = service.artifacts.put(
        "root",
        "selection",
        canonical_json(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "source_id": original.artifact_id,
                        "original_sha256": original.sha256,
                        "layout_ref": layout.model_dump(mode="json"),
                        "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 32, 24]}],
                        "keep_elements": [],
                        "remove_elements": [],
                    }
                ],
            }
        ).encode(),
        role="selection",
    )
    request = UIAnalysisRequest(
        input={"kind": "manual", "inputs": [original]},
        output_mode="reconstruct",
        reconstruction_target="scene_background",
        selection_mode="bound",
        selection_ref=selection,
        allow_local_revision=True,
        budget=BUDGET,
        model_bindings={"canonical_ref": canonical, "layout_ref": layout, "texts_ref": texts},
    )
    root = service.ui_plan("root", request)
    monkeypatch.setattr(cli, "service", lambda: service)
    monkeypatch.setattr(runtime, "freeze_models", lambda *args, **kwargs: pytest.fail("revision CLI loaded a model"))
    return service, root


def _invoke(*args):
    result = CliRunner().invoke(app, ["--json", "ui", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.fixture
def revision_root(tmp_path, monkeypatch):
    return _root_fixture(tmp_path, monkeypatch)


def test_revise_emits_revision_plan_and_child_approval(revision_root):
    service, root = revision_root
    output = _invoke(
        "revise",
        root.task_id,
        "--action",
        "reread_text",
        "--target-id",
        "text-1",
        "--base-revision",
        "0",
    )
    assert output["root_task_id"] == root.task_id
    revision_ref = ArtifactRef.model_validate(output["revision_ref"])
    assert revision_ref.task_id == root.task_id
    assert revision_ref.role == "revision_plan"
    assert len(output["pending_approvals"]) == 1
    child = service.ledger.plan(output["pending_approvals"][0]["task_id"])
    assert output["pending_approvals"][0]["plan_fingerprint"] == child.fingerprint
    assert output["impact"]["proposal_only"] is True
    assert output["artifacts"]

    record = json.loads(service.artifacts.read(revision_ref))
    assert record["child_task_id"] == child.task_id
    request_ref = ArtifactRef.model_validate(record["request_ref"])
    assert request_ref.role == "revision_request"
    assert request_ref.task_id == root.task_id
    assert record["child"]["task_id"] == record["child_task_id"]


def test_child_execute_routes_exact_revision_ref_to_root_workflow(revision_root, monkeypatch):
    service, root = revision_root
    output = _invoke(
        "revise",
        root.task_id,
        "--action",
        "reread_text",
        "--target-id",
        "text-1",
    )
    revision_ref = ArtifactRef.model_validate(output["revision_ref"])
    child_task_id = output["pending_approvals"][0]["task_id"]
    child = service.ledger.plan(child_task_id)
    received = []

    async def start(instance, root_plan, receipt, *, revision_ref=None):
        received.append((root_plan.task_id, receipt.task_id, revision_ref))

    monkeypatch.setattr(cli, "start_approved", start)
    monkeypatch.setattr(runtime, "preflight_child", lambda *args: None)
    executed = _invoke("execute", child.task_id, "--approve", child.fingerprint)
    assert executed["root_task_id"] == root.task_id
    assert len(received) == 1
    received_root, received_child, received_ref = received[0]
    assert (received_root, received_child) == (root.task_id, child.task_id)
    assert (received_ref.task_id, received_ref.role, received_ref.key, received_ref.sha256) == (
        revision_ref.task_id,
        revision_ref.role,
        revision_ref.key,
        revision_ref.sha256,
    )


def test_inspect_local_lists_pending_revision_without_model_or_provider_calls(revision_root):
    service, root = revision_root
    planned = _invoke(
        "revise",
        root.task_id,
        "--action",
        "reread_text",
        "--target-id",
        "text-1",
    )
    revision_ref = ArtifactRef.model_validate(planned["revision_ref"])
    child_task_id = planned["pending_approvals"][0]["task_id"]

    inspected = _invoke("inspect", root.task_id, "--local")

    assert len(inspected["revisions"]) == 1
    revision = inspected["revisions"][0]
    inspected_ref = ArtifactRef.model_validate(revision["revision_ref"])
    assert (inspected_ref.task_id, inspected_ref.key, inspected_ref.sha256) == (
        revision_ref.task_id,
        revision_ref.key,
        revision_ref.sha256,
    )
    assert revision["child_task_id"] == child_task_id
    assert revision["action"] == "reread_text"
    assert revision["target_ids"] == ["text-1"]
    assert revision["state"] == "awaiting_approval"
    assert revision["manifests"] == []
    assert service.ledger.list_operations(root.task_id) == []


@pytest.fixture
def reviewed_v5(tmp_path, monkeypatch):
    service, repository, _budget = reviewed_fixture.__wrapped__(tmp_path, monkeypatch)
    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    return service, repository


def test_reviewed_plan_freezes_model_policies_only_when_local_revision_is_enabled(
    tmp_path, monkeypatch,
):
    service, _, budget = reviewed_fixture.__wrapped__(tmp_path, monkeypatch)
    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    calls = []

    def freeze(store, task_id):
        calls.append(task_id)
        return {"ocr": store.put(task_id, "model", b"frozen-ocr-policy", role="model")}

    monkeypatch.setattr(runtime, "freeze_models", freeze)
    result = _invoke("plan", "--reviewed-task", "reviewed-source", "--mode", "decompose",
                     "--budget", str(budget), "--allow-local-revision")
    assert calls == [result["task_id"]]
    root = service.ledger.plan(result["task_id"])
    request = UIAnalysisRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(root.parameters["request_ref"]))
    )
    assert request.allow_local_revision
    assert request.model_bindings["ocr"].task_id == root.task_id
    assert request.model_bindings["layout_ref"].role == "review_layout"
    assert not service.ledger.list_operations(root.task_id)


def _suggestion(service, repository):
    binding = repository.binding("reviewed-source", 0)
    return save_suggestion(
        service,
        binding,
        ["icon"],
        [{"action": "update_box", "element_id": "icon", "bbox": [2, 2, 9, 9]}],
        [binding.layout_ref],
    )


def test_revision_accept_is_idempotent_for_the_same_request(reviewed_v5):
    service, repository = reviewed_v5
    suggestion = _suggestion(service, repository)
    first = _invoke(
        "revision-accept", "reviewed-source", "--suggestion", suggestion.artifact_id, "--request-id", "accept-1"
    )
    second = _invoke(
        "revision-accept", "reviewed-source", "--suggestion", suggestion.artifact_id, "--request-id", "accept-1"
    )
    assert first["result"]["state"] == "accepted"
    assert second["result"] == first["result"]
    assert repository.head("reviewed-source")["draft_revision"] == 1


def test_revision_accept_rejects_a_suggestion_after_a_lock_change(reviewed_v5):
    service, repository = reviewed_v5
    suggestion = _suggestion(service, repository)
    repository.save(
        "reviewed-source",
        ReviewPatch(
            request_id="lock-icon",
            base_draft_revision=0,
            base_confirmed_revision=0,
            actions=[{"action": "set_lock", "element_id": "icon", "fields": ["bbox"]}],
        ),
    )
    result = CliRunner().invoke(
        app,
        [
            "--json",
            "ui",
            "revision-accept",
            "reviewed-source",
            "--suggestion",
            suggestion.artifact_id,
            "--request-id",
            "accept-after-lock",
        ],
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "review_conflict"
    assert repository.head("reviewed-source")["draft_revision"] == 1
