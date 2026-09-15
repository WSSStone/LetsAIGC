"""T032 editing descendants keep image scope and one batch budget."""

import json

import pytest
from test_ui_batch import family, run_child
from test_ui_child_approvals import FIXTURE, authorize

from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.pipeline import Cost, canonical_json
from letsaigc.schemas.ui import UIResourceLimits, UIStepBinding
from letsaigc.ui_analysis.editing import EditingExecution
from letsaigc.ui_analysis.execution import UIExecution


def segment(service, image, task_id, *, revision=0, source_id=None, selection_scope=None, parent_task_id=None):
    with service.ledger.transaction() as db:
        row = db.execute("SELECT source_ids FROM ui_child_bindings WHERE task_id=?", (image.task_id,)).fetchone()
    source_id = source_id or json.loads(row[0])[0]
    original = next(ref for ref in image.inputs if ref.role == "original")
    value = json.loads(FIXTURE.read_text())["selection"]
    value["sources"][0].update(source_id=source_id, original_sha256=original.sha256)
    value["sources"][0]["target_regions"][0]["xyxy"] = [revision, 0, 48, 32]
    selection = service.artifacts.put(selection_scope or image.task_id, f"selection-{revision}",
                                      canonical_json(value).encode(), role="selection")
    local = service.artifacts.put(task_id, "input", canonical_json(value).encode(), role="selection")
    canonical = service.artifacts.put(task_id, "input", service.artifacts.read(original), role="canonical",
                                      media_type="image/png")
    snapshot = service.artifacts.put(task_id, "model", b'{"fixture":true}', role="model_snapshot")
    request = service.artifacts.put(task_id, "request", canonical_json({
        "schema_version": 1, "canonical_ref": canonical.model_dump(mode="json"),
        "selection_ref": local.model_dump(mode="json"),
        "selection_revision": revision, "selection_hash": local.sha256,
        "prompts": [{"element_id": "region-1", "box": [revision, 0, 48, 32], "points": []}],
        "model_snapshot_ref": snapshot.model_dump(mode="json"), "prompt_version": "fixture-v1",
        "resources": UIResourceLimits().model_dump(mode="json"), "result_roles": ["mask", "alpha"],
    }).encode(), role="request")
    return service.ui_child_plan(task_id, parent_task_id=parent_task_id or image.task_id, purpose="segmentation",
                                 source_ids=[source_id], request_ref=request, selection_ref=selection,
                                 selection_revision=revision, budget=image.envelope.budget)


def reserve(service, child, *, cost=0):
    binding = EditingExecution(service).binding(child)
    return service.ledger.reserve(child, binding.step_id, 0, Cost(cost_usd=cost), ui_binding=binding)


def test_gpu_child_uses_image_owner_and_requires_exact_approval(tmp_path):
    service, root, batch = family(tmp_path)
    image = batch.prepare(root).child
    child = segment(service, image, "gpu")
    assert child.parameters["root_task_id"] == root.task_id
    assert EditingExecution(service).child_root(child) == image.task_id
    with pytest.raises(PipelineError, match="approval"):
        reserve(service, child)
    authorize(service, child)
    reserve(service, child)
    with service.ledger.transaction() as db:
        ancestor = db.execute(
            "SELECT active,status FROM ui_child_bindings WHERE task_id=?", (image.task_id,)).fetchone()
        charge = db.execute("SELECT root_task_id,source_chain FROM ui_operation_charges").fetchone()
    assert tuple(ancestor) == (1, "running")
    assert charge["root_task_id"] == root.task_id
    assert json.loads(charge["source_chain"]) == [next(ref.sha256 for ref in image.inputs if ref.role == "original")]
    assert EditingExecution(service).status(image).child.task_id == child.task_id


def test_batch_images_share_generation_revision_limit(tmp_path):
    service, root, batch = family(tmp_path)
    first = batch.prepare(root).child
    for revision in range(3):
        child = segment(service, first, f"first-gpu-{revision}", revision=revision)
        authorize(service, child)
        operation = reserve(service, child)
        service.ledger.finish(operation.operation_id, Cost(), {})
    batch.complete(root, run_child(service, first))
    second = batch.prepare(root).child
    initial = segment(service, second, "second-initial")
    authorize(service, initial)
    service.ledger.finish(reserve(service, initial).operation_id, Cost(), {})
    extra = segment(service, second, "second-revision", revision=1)
    authorize(service, extra)
    with pytest.raises(PipelineError) as caught:
        reserve(service, extra)
    assert caught.value.code == "revision_limit"


def test_child_cannot_use_batch_selection_or_another_image_source(tmp_path):
    service, root, batch = family(tmp_path)
    image = batch.prepare(root).child
    with pytest.raises(PipelineError) as caught:
        segment(service, image, "batch-scope", selection_scope=root.task_id)
    assert caught.value.code == "artifact_scope"
    with pytest.raises(PipelineError) as caught:
        segment(service, image, "wrong-source", source_id="foreign-source")
    assert caught.value.code == "source_scope"


def test_completed_analysis_cannot_append_new_operations(tmp_path):
    service, root, batch = family(tmp_path)
    image = batch.prepare(root).child
    batch.complete(root, run_child(service, image))
    runner = UIExecution(service, evidence_kind="offline")
    prepared = runner.prepare(image, "ocr", 0)
    binding = UIStepBinding.model_validate({**prepared.binding.model_dump(mode="json"), "revision": 1})
    with pytest.raises(PipelineError) as caught:
        service.ledger.reserve(image, binding.step_id, 1, Cost(), ui_binding=binding, ui_request=runner.request(image))
    assert caught.value.code == "inactive_child"


def local_child(service, image, task_id, *, action):
    from letsaigc.schemas.ui import ImageView, UIAnalysisRequest
    from letsaigc.ui_analysis.revision import RevisionRequest
    from letsaigc.ui_analysis.revision_inputs import LocalRevisionInputs

    request = UIExecution(service).request(image)
    with service.ledger.transaction() as db:
        source_id = json.loads(db.execute("SELECT source_ids FROM ui_child_bindings WHERE task_id=?",
                                          (image.task_id,)).fetchone()[0])[0]
    original = next(ref for ref in image.inputs if ref.role == "original")
    def put(role, value):
        return service.artifacts.put(task_id, "input", canonical_json(value).encode(), role=role)
    canonical = service.artifacts.put(task_id, "input", service.artifacts.read(original),
                                      role="canonical", media_type="image/png")
    local_original = service.artifacts.put(task_id, "input", service.artifacts.read(original),
                                           role="original", media_type="image/png")
    value = json.loads(FIXTURE.read_text())["selection"]
    value["sources"][0].update(source_id=source_id, original_sha256=original.sha256)
    selection = service.artifacts.put(image.task_id, "selection", canonical_json(value).encode(), role="selection")
    local_selection = put("selection", value)
    context = UIAnalysisRequest.model_validate(request.model_dump(mode="json") | {
        "input": {"kind": "manual", "inputs": [local_original.model_dump(mode="json")]},
        "selection_mode": "bound", "selection_ref": local_selection.model_dump(mode="json"),
        "model_bindings": {"canonical_ref": canonical.model_dump(mode="json"),
                           "ocr": put("model", {"fixture": True}).model_dump(mode="json")},
    })
    revision = RevisionRequest(base_task_id=image.task_id, base_fingerprint=image.fingerprint,
                               base_revision=0, action=action, target_ids=["panel"], parameters={})
    view = ImageView(view_id="local-view", kind="local", canonical_ref=canonical, input_ref=canonical,
                     width=48, height=32, crop=(0, 0, 48, 32),
                     forward=((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)),
                     inverse=((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)))
    wrapper = LocalRevisionInputs(action=action, analysis_request_ref=put("request", context),
                                   revision_request_ref=put("revision_request", revision),
                                   selection_ref=local_selection, selection_revision=0,
                                   view_ref=put("view_manifest", view), texts_ref=put("texts", {}),
                                   parameters_ref=put("parameters", {}))
    return service.ui_child_plan(task_id, parent_task_id=image.task_id, purpose=action, source_ids=[source_id],
                                 request_ref=put("request", wrapper), selection_ref=selection,
                                 selection_revision=0, budget=image.envelope.budget)


@pytest.mark.parametrize("action,allowed", [("reread_text", 2), ("review_region", 1)])
def test_local_calls_accumulate_per_image_and_include_initial_vlm(tmp_path, action, allowed):
    service, root, batch = family(tmp_path, request_overrides={
        "allow_local_revision": True, "limits": {"vlm_calls_per_image": 2},
        "output_mode": "reconstruct", "reconstruction_target": "scene_background",
        "selection_mode": "deferred",
    })
    for image_index in range(2):
        image = batch.prepare(root).child
        result = run_child(service, image)
        for index in range(allowed + 1):
            child = local_child(service, image, f"local-{image_index}-{index}", action=action)
            authorize(service, child)
            binding = EditingExecution(service).binding(child)
            def submit(child=child, binding=binding, image=image):
                return service.ledger.reserve(child, binding.step_id, 0, Cost(cost_usd=0.1), ui_binding=binding,
                                               ui_request=UIExecution(service).request(image))
            if index == allowed:
                with pytest.raises(PipelineError) as caught:
                    submit()
                assert caught.value.code == "call_limit"
            else:
                service.ledger.finish(submit().operation_id, Cost(cost_usd=0.1), {})
        if action == "review_region":
            runner = UIExecution(service, evidence_kind="offline")
            initial = runner.prepare(image, "analyze", 0).binding.model_copy(update={"revision": 1})
            with pytest.raises(PipelineError) as caught:
                service.ledger.reserve(image, initial.step_id, 1, Cost(), ui_binding=initial,
                                       ui_request=runner.request(image))
            assert caught.value.code == "call_limit"
        batch.complete(root, result)
    assert service.ledger.usage(root.task_id, include_children=True)["actual"].cost_usd == pytest.approx(allowed * 0.2)


def test_image_selection_change_supersedes_its_editing_children(tmp_path):
    service, root, batch = family(tmp_path)
    image = batch.prepare(root).child
    child = segment(service, image, "pending-gpu")
    with service.ledger.selection_change(image.task_id):
        pass
    with pytest.raises(PipelineError) as caught:
        authorize(service, child)
    assert caught.value.code == "selection_superseded"
    with service.ledger.transaction() as db:
        assert db.execute("SELECT active FROM ui_child_bindings WHERE task_id=?", (image.task_id,)).fetchone()[0] == 1


def test_completed_image_cannot_activate_previously_planned_gpu_child(tmp_path):
    service, root, batch = family(tmp_path)
    image = batch.prepare(root).child
    child = segment(service, image, "late-gpu")
    batch.complete(root, run_child(service, image))
    with pytest.raises(PipelineError) as caught:
        authorize(service, child)
    assert caught.value.code == "inactive_child"


def test_original_ocr_rereads_also_consume_image_local_call_allowance(tmp_path):
    service, root, batch = family(tmp_path, request_overrides={
        "allow_local_revision": True, "resources": {"ocr_max_tiles": 1},
        "output_mode": "reconstruct", "reconstruction_target": "scene_background", "selection_mode": "deferred",
    })
    image = batch.prepare(root).child
    run_child(service, image)
    runner = UIExecution(service, evidence_kind="offline")
    initial = runner.prepare(image, "ocr", 0).binding
    for revision in (1, 2):
        binding = initial.model_copy(update={"revision": revision})
        operation = service.ledger.reserve(image, binding.step_id, revision, Cost(), ui_binding=binding,
                                           ui_request=runner.request(image))
        service.ledger.finish(operation.operation_id, Cost(), {})
    child = local_child(service, image, "exhausted-ocr", action="reread_text")
    authorize(service, child)
    binding = EditingExecution(service).binding(child)
    with pytest.raises(PipelineError) as caught:
        service.ledger.reserve(child, binding.step_id, 0, Cost(), ui_binding=binding, ui_request=runner.request(image))
    assert caught.value.code == "call_limit"
