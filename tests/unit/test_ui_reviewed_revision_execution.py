"""Reviewed-layout provenance tests for local T029 revision execution."""

from __future__ import annotations

import json

import pytest
from test_ui_revision_review import reviewed as reviewed_fixture

from letsaigc.pipelines.approval import approve
from letsaigc.schemas.pipeline import ArtifactRef, Cost, canonical_json
from letsaigc.schemas.ui import UIAnalysisRequest
from letsaigc.schemas.ui_provider import UIInputManifest
from letsaigc.ui_analysis import cli
from letsaigc.ui_analysis import selection as selection_tools
from letsaigc.ui_analysis.editing import EditingExecution
from letsaigc.ui_analysis.revision import RevisionRequest
from letsaigc.ui_analysis.revision_execution import RevisionExecution
from letsaigc.ui_analysis.revision_review import accept_suggestion

BUDGET = {
    "max_total_cost_usd": 1.0,
    "max_iteration_cost_usd": 0.5,
    "max_total_gpu_minutes": 0,
    "max_iteration_gpu_minutes": 0,
    "max_revisions": 2,
}


def _selection(service, task_id, inputs, refs):
    manifest = UIInputManifest.model_validate_json(service.artifacts.read(inputs.metadata_ref))
    source = manifest.sources[0]
    trusted = selection_tools.TrustedSource(
        source.source_id,
        source.original_ref,
        width=16,
        height=12,
        layout_ref=refs["layout_ref"],
        canonical_ref=refs["canonical_ref"],
    )
    value = {
        "schema_version": 1,
        "sources": [
            {
                "source_id": source.source_id,
                "original_sha256": source.original_ref.sha256,
                "layout_ref": refs["layout_ref"].model_dump(mode="json"),
                "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 16, 12]}],
                "keep_elements": [],
                "remove_elements": [],
            }
        ],
    }
    prepared = selection_tools.register_selection(
        service.artifacts,
        task_id,
        value,
        {source.source_id: trusted},
        operation_id="review-selection",
    )
    selection_tools.record_selection(service.artifacts, task_id, prepared.selection_ref)
    return prepared.selection_ref


@pytest.fixture
def reviewed_root(tmp_path):
    service, repository, binding, _evidence = reviewed_fixture.__wrapped__(tmp_path)
    inputs, _binding_data, refs = cli._reviewed_input(service, "newroot", "review-task", 0)
    fake_ocr = service.artifacts.put("newroot", "model", b"fake-ocr", role="model")
    model_bindings = {**refs, "ocr": fake_ocr}
    selection_ref = _selection(service, "newroot", inputs, refs)
    request = UIAnalysisRequest(
        input=inputs,
        output_mode="decompose",
        selection_mode="deferred",
        selection_ref=None,
        allow_local_revision=True,
        budget=BUDGET,
        model_bindings=model_bindings,
    )
    root = service.ui_plan("newroot", request)
    return service, repository, binding, root, request, refs, selection_ref


def _revision(root, action, target_ids):
    return RevisionRequest(
        base_task_id=root.task_id,
        base_fingerprint=root.fingerprint,
        base_revision=0,
        action=action,
        target_ids=target_ids,
        parameters={},
    )


def test_local_vlm_adapter_reads_child_scoped_policy_and_review_context(reviewed_root, monkeypatch):
    from letsaigc.agent import ui_analyzer
    from letsaigc.ui_analysis.runtime import freeze_models

    service, _, _, _, _, _, _ = reviewed_root
    inputs, _, refs = cli._reviewed_input(service, "vlmroot", "review-task", 0)
    models = freeze_models(service.artifacts, "vlmroot")
    _selection(service, "vlmroot", inputs, refs)
    root = service.ui_plan("vlmroot", UIAnalysisRequest(
        input=inputs, output_mode="decompose", selection_mode="deferred",
        allow_local_revision=True, budget=BUDGET, model_bindings={**models, **refs},
    ))
    prepared = RevisionExecution(service).plan(_revision(root, "review_region", ["label"]))
    child = prepared.child
    seen = []

    def analyze(_self, _store, view, texts, *, user_notes):
        seen.append(texts)
        assert view.input_ref.task_id == child.task_id
        assert texts["texts"][0]["text_id"] == "ocr-1"
        assert texts["texts"][0]["bbox"] == [0, 0, 6, 4]
        return {"response_id": "fake-response", "state": "succeeded", "actual": Cost().model_dump(mode="json")}

    monkeypatch.setattr(ui_analyzer.UIAnalyzer, "analyze", analyze)
    adapter = ui_analyzer.UIAnalysisBackend(service.ledger, service.artifacts)
    binding = EditingExecution(service).binding(child)
    submission = adapter.submit("fake-local-vlm", {"binding": binding.model_dump(mode="json")})
    assert submission.request_id == "fake-response"
    assert len(seen) == 1


def test_multiple_review_texts_keep_one_crop_origin_and_clip_at_image_edges(reviewed_root):
    from letsaigc.ui_analysis.revision_execution import _local_texts

    service = reviewed_root[0]
    raw = canonical_json({"texts": [
        {"text_region_id": "one", "effective_text": "one", "bbox": [10, 10, 15, 15]},
        {"text_region_id": "two", "effective_text": "two", "bbox": [20, 20, 40, 40]},
        {"text_region_id": "outside", "effective_text": "out", "bbox": [0, 0, 5, 5]},
    ]}).encode()
    source = service.artifacts.put("review-task", "more-texts", raw, role="review_texts")
    context = _local_texts(service, source, "newroot", "context", (10, 10, 30, 30))
    texts = json.loads(service.artifacts.read(context))["texts"]
    assert [(row["text_id"], row["bbox"]) for row in texts] == [
        ("one", [0, 0, 5, 5]), ("two", [10, 10, 20, 20]),
    ]
    assert service.artifacts.read(source) == raw


def _complete_child(service, root_request, child, payload, role):
    execution = EditingExecution(service)
    approval = approve(service.ledger, child.task_id, child.fingerprint)
    service.ledger.consume_approval(approval)
    binding = execution.binding(child)
    operation = service.ledger.reserve(
        child,
        binding.step_id,
        0,
        Cost(),
        ui_binding=binding,
        ui_request=root_request,
    )
    output = service.artifacts.put(
        child.task_id,
        operation.operation_id,
        canonical_json(payload).encode(),
        role=role,
        source_ids=[item.artifact_id for item in child.inputs[:1]],
    )
    service.ledger.finish_ui(
        operation.operation_id,
        Cost(),
        {"artifacts": [output.model_dump(mode="json")]},
    )
    return output


def _saved_suggestion(service, execution, root, revision, root_request, payload, role):
    planned = execution.plan(revision)
    assert planned.child is not None
    _complete_child(service, root_request, planned.child, payload, role)
    finished = execution.prepare(root, planned.revision_ref)
    assert finished.state == "succeeded"
    manifest_ref = next(ref for ref in finished.artifacts if ref.role == "revision_manifest")
    manifest = json.loads(service.artifacts.read(manifest_ref))
    assert manifest["suggestions"]
    assert manifest["suggestions"][0]["state"] == "saved"
    return planned, finished, manifest


def test_reviewed_reread_maps_fresh_ocr_to_stable_text_and_accepts_suggestion(reviewed_root):
    service, repository, binding, root, root_request, refs, _selection_ref = reviewed_root
    original_review = {
        name: service.artifacts.read(ref)
        for name, ref in {
            "canonical": binding.canonical_ref,
            "layout": binding.layout_ref,
            "texts": binding.texts_ref,
            "manifest": binding.review_manifest_ref,
        }.items()
    }
    revision = _revision(root, "reread_text", ["ocr-1"])
    execution = RevisionExecution(service)
    planned = execution.plan(revision)
    assert planned.child is not None
    wrapper_ref = ArtifactRef.model_validate(planned.child.parameters["request_ref"])
    wrapper = json.loads(service.artifacts.read(wrapper_ref))
    view = json.loads(service.artifacts.read(ArtifactRef.model_validate(wrapper["view_ref"])))
    assert view["crop"] == [2, 2, 8, 6]
    local_texts = json.loads(service.artifacts.read(ArtifactRef.model_validate(wrapper["texts_ref"])))
    assert local_texts["texts"][0]["text_id"] == "ocr-1"
    assert local_texts["texts"][0]["bbox"] == [0, 0, 6, 4]
    assert wrapper["selection_ref"]["task_id"] == planned.child.task_id

    _complete_child(
        service,
        root_request,
        planned.child,
        {
            "schema_version": 1,
            "status": "observed",
            "texts": [{"text_id": "fresh-ocr-1", "text": "新文本", "bbox": [0, 0, 6, 4]}],
        },
        "texts",
    )
    finished = execution.prepare(root, planned.revision_ref)
    manifest_ref = next(ref for ref in finished.artifacts if ref.role == "revision_manifest")
    manifest = json.loads(service.artifacts.read(manifest_ref))
    suggestion = manifest["suggestions"][0]
    assert suggestion["state"] == "saved"
    suggestion_ref = ArtifactRef.model_validate(suggestion["ref"])
    suggestion_payload = json.loads(service.artifacts.read(suggestion_ref))
    assert suggestion_payload["actions"][0]["text_region_id"] == "ocr-1"
    assert repository.head("review-task")["draft_revision"] == 0

    result = accept_suggestion(
        service,
        suggestion_ref,
        "accept-reread",
        base_draft_revision=0,
        base_confirmed_revision=0,
    )
    assert result["state"] == "accepted"
    assert repository.head("review-task")["confirmed_revision"] == 0
    assert repository.document("review-task", 0).texts[0].effective_text == "原文"
    assert repository.document("review-task", 1).texts[0].effective_text == "新文本"
    assert repository.document("review-task", 1).texts[0].origin == "human"
    assert {
        name: service.artifacts.read(ref)
        for name, ref in {
            "canonical": binding.canonical_ref,
            "layout": binding.layout_ref,
            "texts": binding.texts_ref,
            "manifest": binding.review_manifest_ref,
        }.items()
    } == original_review


def test_reviewed_region_vlm_suggestion_uses_confirmed_binding(reviewed_root):
    service, repository, binding, root, root_request, _refs, _selection_ref = reviewed_root
    revision = _revision(root, "review_region", ["label"])
    planned, finished, manifest = _saved_suggestion(
        service,
        RevisionExecution(service),
        root,
        revision,
        root_request,
        {
            "schema_version": 1,
            "output": {"correction_suggestions": [{"text_id": "ocr-1", "suggestion": "区域建议"}]},
        },
        "analysis",
    )
    assert planned.child is not None
    assert planned.child.workflow_type == "ui_region_revision"
    suggestion = manifest["suggestions"][0]
    assert suggestion["state"] == "saved"
    assert suggestion["ref"]["task_id"] == "review-task"
    accepted = accept_suggestion(
        service,
        ArtifactRef.model_validate(suggestion["ref"]),
        "accept-region",
        base_draft_revision=0,
        base_confirmed_revision=0,
    )
    assert accepted["state"] == "accepted"
    assert repository.head("review-task")["confirmed_revision"] == 0
    assert repository.document("review-task", 1).texts[0].effective_text == "区域建议"
