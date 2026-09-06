"""Automatic parse rebinding stays offline and task scoped."""

import json
from io import BytesIO

import pytest
from PIL import Image

from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef, PipelineRun, canonical_json
from letsaigc.schemas.ui import CanonicalImage, UIAnalysisRequest
from letsaigc.schemas.ui_provider import (
    ManualUIInput,
    UIInputManifest,
    UIProvisionResult,
    UISource,
)
from letsaigc.ui_analysis import edit_inputs


def _png(color="red"):
    stream = BytesIO()
    Image.new("RGB", (20, 20), color).save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def automatic_fixture(tmp_path, monkeypatch):
    service = PipelineService(tmp_path / "state", ui_schema=4)
    store = service.artifacts
    task_id = "automatic-source"
    original = store.put(task_id, "input", _png(), role="original", media_type="image/png")
    provenance = store.put(
        task_id,
        "input",
        canonical_json({"origin": "fixture", "sha256": original.sha256}).encode(),
        role="provenance",
        source_ids=[original.artifact_id],
    )
    source = UISource(
        source_id="source-automatic",
        original_ref=original,
        provenance_ref=provenance,
        input_entry_ids=["input-1"],
    )
    input_manifest = UIInputManifest(
        task_id=task_id,
        status="ready",
        entries=[{"entry_id": "input-1", "status": "ready", "source_id": source.source_id}],
        sources=[source],
    )
    input_manifest_ref = store.put(task_id, "input", canonical_json(input_manifest).encode(), role="input_manifest")
    request = UIAnalysisRequest(
        input=ManualUIInput(inputs=[original], metadata_ref=input_manifest_ref),
        budget={
            "max_total_cost_usd": 1,
            "max_iteration_cost_usd": 1,
            "max_total_gpu_minutes": 0,
            "max_iteration_gpu_minutes": 0,
            "max_revisions": 0,
        },
    )
    plan = service.ui_plan(task_id, request)
    canonical_ref = store.put(task_id, "normalize", _png(), role="canonical", media_type="image/png")
    canonical = CanonicalImage(
        source_id=source.source_id,
        original_ref=original,
        canonical_ref=canonical_ref,
        width=20,
        height=20,
    )
    canonical_index_ref = store.put(
        task_id,
        "normalize",
        canonical_json({"canonical": canonical.model_dump(mode="json"), "views": []}).encode(),
        role="canonical_index",
    )
    texts_ref = store.put(
        task_id,
        "analyze",
        canonical_json(
            {"schema_version": 1, "status": "empty", "texts": [], "canonical_sha256": canonical_ref.sha256}
        ).encode(),
        role="texts",
    )
    layout_ref = store.put(
        task_id,
        "layout",
        canonical_json(
            {
                "schema_version": 1,
                "source_id": source.source_id,
                    "canonical_ref": canonical_ref.model_dump(mode="json"),
                "canonical_sha256": canonical_ref.sha256,
                "width": 20,
                "height": 20,
                "coordinate_space": "canonical_px_xyxy_exclusive_max",
                "revision_id": 0,
                "elements": [{"element_id": "whole", "kind": "image", "bbox": [0, 0, 20, 20]}],
                    "texts_ref": texts_ref.model_dump(mode="json"),
            }
        ).encode(),
        role="layout",
    )
    sources_ref = store.put(
        task_id,
        "provide",
        canonical_json(
            UIProvisionResult(
                provider_id="manual",
                provider_version="1",
                status="ready",
                sources=[source],
                index_ref=input_manifest_ref,
            )
        ).encode(),
        role="sources",
    )
    declared = [sources_ref, canonical_ref, canonical_index_ref, texts_ref, layout_ref]
    manifest_ref = store.put(
        task_id,
        "project",
        canonical_json(
            {
                "schema_version": 1,
                "kind": "ui_analysis",
                "task_id": task_id,
                "plan_fingerprint": plan.fingerprint,
                "workflow_type": plan.workflow_type,
                "inputs": [ref.model_dump(mode="json") for ref in plan.inputs],
                "outputs": [ref.model_dump(mode="json") for ref in declared],
                "operations_ref": None,
            }
        ).encode(),
        role="manifest",
    )
    run = PipelineRun(
        task_id=task_id,
        workflow_id=task_id,
        temporal_run_id="run-automatic",
        plan_fingerprint=plan.fingerprint,
        state="succeeded",
        artifacts=[*declared, manifest_ref],
    )
    with service.ledger.transaction() as db:
        db.execute(
            "INSERT INTO projections(task_id,sequence,payload) VALUES(?,?,?)",
            (task_id, 1, run.model_dump_json()),
        )

    class FakeExecution:
        def __init__(self, _service):
            pass

        def request(self, _plan):
            return request

        def outputs(self, _plan):
            return [*declared, manifest_ref]

    monkeypatch.setattr(edit_inputs, "UIExecution", FakeExecution)
    return service, task_id, original, canonical_ref, layout_ref, texts_ref, manifest_ref


def test_automatic_input_copies_frozen_parse_without_ledger_writes(automatic_fixture):
    service, source_task, original, canonical, layout, texts, source_manifest = automatic_fixture
    inputs, info, refs = edit_inputs.automatic_input(service, "automatic-child", source_task)

    assert inputs.kind == "manual"
    assert inputs.inputs[0].task_id == "automatic-child"
    assert set(refs) == {"canonical_ref", "layout_ref", "texts_ref", "automatic_manifest_ref"}
    assert all(ref.task_id == "automatic-child" for ref in refs.values())
    assert info["source_task_id"] == source_task
    assert info["source_fingerprint"]
    assert info["model_calls"] == 0
    assert service.artifacts.read(inputs.inputs[0]) == service.artifacts.read(original)
    rebound_layout = json.loads(service.artifacts.read(refs["layout_ref"]))
    assert ArtifactRef.model_validate(rebound_layout["canonical_ref"]) == refs["canonical_ref"]
    assert ArtifactRef.model_validate(rebound_layout["texts_ref"]) == refs["texts_ref"]
    manifest = json.loads(service.artifacts.read(refs["automatic_manifest_ref"]))
    assert manifest["layout_origin"] == "automatic"
    assert manifest["source_task_id"] == source_task
    assert service.ledger.list_operations(source_task) == []
    assert service.ledger.list_operations("automatic-child") == []
    assert service.artifacts.read(canonical) == service.artifacts.read(refs["canonical_ref"])
    assert service.artifacts.read(layout) != service.artifacts.read(refs["layout_ref"])
    assert service.artifacts.read(texts) == service.artifacts.read(refs["texts_ref"])
    assert source_manifest.task_id == source_task


@pytest.mark.parametrize("state", ["planned", "failed"])
def test_automatic_input_rejects_non_succeeded_projection(automatic_fixture, state):
    service, source_task, *_ = automatic_fixture
    with service.ledger.transaction() as db:
        raw = json.loads(db.execute("SELECT payload FROM projections WHERE task_id=?", (source_task,)).fetchone()[0])
        raw["state"] = state
        db.execute("UPDATE projections SET payload=? WHERE task_id=?", (json.dumps(raw), source_task))
    with pytest.raises(PipelineError) as caught:
        edit_inputs.automatic_input(service, "automatic-child", source_task)
    assert caught.value.code == "automatic_input_unavailable"


def test_automatic_input_rejects_fingerprint_tampering(automatic_fixture):
    service, source_task, *_ = automatic_fixture
    with service.ledger.transaction() as db:
        raw = json.loads(db.execute("SELECT payload FROM projections WHERE task_id=?", (source_task,)).fetchone()[0])
        raw["plan_fingerprint"] = "0" * 64
        db.execute("UPDATE projections SET payload=? WHERE task_id=?", (json.dumps(raw), source_task))
    with pytest.raises(PipelineError):
        edit_inputs.automatic_input(service, "automatic-child", source_task)


def test_automatic_input_rejects_non_parse_request(automatic_fixture, monkeypatch):
    service, source_task, *_ = automatic_fixture

    class NonParseExecution(edit_inputs.UIExecution):
        def request(self, _plan):
            return super().request(_plan).model_copy(
                update={"output_mode": "decompose", "selection_mode": "deferred"}
            )

    monkeypatch.setattr(edit_inputs, "UIExecution", NonParseExecution)
    with pytest.raises(PipelineError) as caught:
        edit_inputs.automatic_input(service, "automatic-child", source_task)
    assert caught.value.code == "automatic_input_unavailable"


def test_automatic_input_rechecks_original_hash(automatic_fixture):
    service, source_task, original, *_ = automatic_fixture
    service.artifacts.resolve(original).write_bytes(b"changed")
    with pytest.raises(PipelineError) as caught:
        edit_inputs.automatic_input(service, "automatic-child", source_task)
    assert caught.value.code == "artifact_changed"
