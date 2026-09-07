"""Provider-free coordinator boundaries for the T028 editing workflow.

These tests deliberately stop at synthetic child operations.  They exercise the
ledger transaction boundary and the common UI submit/observe/collect path, but
never start SAM, ComfyUI, a model, or a network service.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from PIL import Image
from test_ui_child_approvals import authorize as authorize_child
from test_ui_child_approvals import family as family_helper
from test_ui_child_approvals import reserve as reserve_child

from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.contracts import Capability, Submission
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import ArtifactRef, Cost, canonical_json, operation_id
from letsaigc.schemas.ui import UIAnalysisRequest, UIObservation, UISegmentationRequest, UISelection
from letsaigc.ui_analysis.editing import EditingExecution


def family(tmp_path):
    """Reuse the established synthetic v5 root/child fixture without a fixture dependency."""

    return family_helper.__wrapped__(tmp_path)


def test_selection_change_invalidates_prepared_child_and_replayed_approval(tmp_path):
    service, root, child = family(tmp_path)
    plan = child("prepared-selection")
    approval = approve(service.ledger, plan.task_id, plan.fingerprint)
    service.ledger.consume_approval(approval)
    operation = reserve_child(service, plan)

    with service.ledger.selection_change(root.task_id):
        pass

    current = service.ledger.get(operation.operation_id)
    assert current.state == "failed"
    assert current.reserved == Cost()
    assert current.result == {"selection_superseded": True}
    with service.ledger.transaction() as db:
        binding = db.execute(
            "SELECT status,active FROM ui_child_bindings WHERE task_id=?", (plan.task_id,)
        ).fetchone()
        assert tuple(binding) == ("selection_superseded", 0)
        assert db.execute(
            "SELECT operation_id FROM resources WHERE operation_id=?", (operation.operation_id,)
        ).fetchone() is None

    # The exact old local approval receipt cannot reactivate a superseded child.
    with pytest.raises(PipelineError) as caught:
        service.ledger.consume_approval(approval)
    assert caught.value.code == "selection_superseded"

    with pytest.raises(PipelineError) as caught:
        service.ledger.begin_submit(operation.operation_id)
    assert caught.value.code in {"approval_required", "selection_superseded"}


@pytest.mark.parametrize("state", ["submitted", "outcome_unknown"])
def test_selection_change_rejects_inflight_child_without_mutating_it(tmp_path, state):
    service, root, child = family(tmp_path)
    plan = child(f"inflight-{state}")
    authorize_child(service, plan)
    operation = reserve_child(service, plan)
    if state == "submitted":
        assert service.ledger.begin_submit(operation.operation_id)
        service.ledger.submitted(operation.operation_id, "provider-request", {"receipt": {}})
    else:
        service.ledger.uncertain(operation.operation_id)

    before = service.ledger.get(operation.operation_id)
    with pytest.raises(PipelineError) as caught:
        with service.ledger.selection_change(root.task_id):
            pass
    assert caught.value.code == "awaiting_reconciliation"
    after = service.ledger.get(operation.operation_id)
    assert after.state == state
    assert after.provider_request_id == ("provider-request" if state == "submitted" else None)
    with service.ledger.transaction() as db:
        binding = db.execute(
            "SELECT status,active FROM ui_child_bindings WHERE task_id=?", (plan.task_id,)
        ).fetchone()
        assert tuple(binding) == ("active", 1)
    usage = service.ledger.usage(root.task_id, include_children=True)
    held = Cost(cost_usd=0.2, gpu_minutes=1)
    assert usage["unsettled"] == (held if state == "outcome_unknown" else Cost())
    assert usage["reserved"] == (held if state == "submitted" else Cost())
    assert after.input_hash == before.input_hash


def test_begin_submit_before_selection_change_closes_the_atomic_change_boundary(tmp_path):
    service, root, child = family(tmp_path)
    plan = child("submit-race")
    authorize_child(service, plan)
    operation = reserve_child(service, plan)
    assert service.ledger.begin_submit(operation.operation_id)

    with pytest.raises(PipelineError) as caught:
        with service.ledger.selection_change(root.task_id):
            pass
    assert caught.value.code == "awaiting_reconciliation"
    assert service.ledger.get(operation.operation_id).state == "submitting"


class SyntheticSegmentationBackend:
    def __init__(self, *, capability_id="ui.segment", output_role="segmentation", release_failure=False):
        self.capability = Capability(id=capability_id, idempotent_submission=True, resource="local-gpu")
        self.output_role = output_role
        self.release_failure = release_failure
        self.submit_calls = 0
        self.inspect_calls = 0
        self.collect_calls = 0
        self.release_calls = 0
        self.events: list[tuple[str, str]] = []

    def submit(self, operation_id, arguments):
        self.submit_calls += 1
        self.events.append(("submit", operation_id))
        return Submission(request_id="synthetic-provider-request", metadata={"task_id": operation_id})

    def inspect(self, submission):
        self.inspect_calls += 1
        return UIObservation(state="succeeded", actual=Cost(gpu_minutes=1))

    def collect(self, submission):
        self.collect_calls += 1
        self.events.append(("collect", submission.request_id))
        return [(self.output_role, b'{"status":"ready"}', "application/json")]

    def release_operation(self, submission):
        self.release_calls += 1
        self.events.append(("release", submission.request_id))
        if self.release_failure:
            raise PipelineError("resource_release_unknown")
        return {"released": True, "device": "cuda"}


class ReadySegmentationBackend(SyntheticSegmentationBackend):
    """Synthetic SAM output with the complete persisted SegmentationBundle contract."""

    def __init__(self, service):
        super().__init__()
        self.service = service

    def submit(self, operation_id, arguments):
        self.submit_calls += 1
        self.events.append(("submit", operation_id))
        return Submission(
            request_id="synthetic-ready-sam",
            metadata={"operation_id": operation_id},
        )

    def collect(self, submission):
        from letsaigc.vision.segmentation import SegmentationAsset, SegmentationBundle

        self.collect_calls += 1
        operation_id = submission.metadata["operation_id"]
        operation = self.service.ledger.get(operation_id)
        plan = self.service.ledger.plan(operation.task_id)
        request_ref = ArtifactRef.model_validate(plan.parameters["request_ref"])
        request = UISegmentationRequest.model_validate_json(self.service.artifacts.read(request_ref))
        selection_ref = ArtifactRef.model_validate(plan.parameters["selection_ref"])
        canonical = Image.open(io.BytesIO(self.service.artifacts.read(request.canonical_ref))).convert("RGB")
        items = []
        for prompt in request.prompts:
            x1, y1, x2, y2 = prompt.box
            bbox = (x1, y1, x2, y2)
            mask = Image.new("L", canonical.size, 0)
            for y in range(y1, y2):
                for x in range(x1, x2):
                    mask.putpixel((x, y), 255)
            alpha = mask.copy()
            rect = canonical.crop(bbox)
            visible = rect.convert("RGBA")
            visible.putalpha(Image.new("L", rect.size, 255))
            refs = {
                "rect_crop": self.service.artifacts.put(
                    plan.task_id, operation_id, _png_bytes(rect), role="rect_crop", media_type="image/png"
                ),
                "contour_mask": self.service.artifacts.put(
                    plan.task_id, operation_id, _png_bytes(mask), role="contour_mask", media_type="image/png"
                ),
                "estimated_alpha": self.service.artifacts.put(
                    plan.task_id, operation_id, _png_bytes(alpha), role="estimated_alpha", media_type="image/png"
                ),
                "visible_crop": self.service.artifacts.put(
                    plan.task_id, operation_id, _png_bytes(visible), role="visible_crop", media_type="image/png"
                ),
            }
            items.append(
                SegmentationAsset(
                    element_id=prompt.element_id,
                    bbox=bbox,
                    status="ready",
                    reason="synthetic_ready",
                    confidence=1,
                    canonical_sha256=request.canonical_ref.sha256,
                    rect_crop_ref=refs["rect_crop"],
                    contour_mask_ref=refs["contour_mask"],
                    estimated_alpha_ref=refs["estimated_alpha"],
                    visible_crop_ref=refs["visible_crop"],
                )
            )
        bundle = SegmentationBundle(
            status="ready",
            task_id=plan.task_id,
            operation_id=operation_id,
            canonical_ref=request.canonical_ref,
            canonical_sha256=request.canonical_ref.sha256,
            selection_ref=selection_ref,
            selection_revision=request.selection_revision,
            selection_hash=selection_ref.sha256,
            model_snapshot_ref=request.model_snapshot_ref,
            model_digest=request.model_snapshot_ref.sha256,
            items=items,
        )
        self.events.append(("collect", submission.request_id))
        return [("segmentation", canonical_json(bundle).encode(), "application/json")]


class SyntheticInpaintBackend(SyntheticSegmentationBackend):
    """Provider-free terminal image backend for the planner continuation."""

    def __init__(self):
        super().__init__(capability_id="ui.inpaint", output_role="image")

    def collect(self, submission):
        self.collect_calls += 1
        self.events.append(("collect", submission.request_id))
        return [("image", _png_bytes(Image.new("RGB", (24, 24), (12, 24, 36))), "image/png")]


def _png_bytes(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=False)
    return stream.getvalue()


def _review_layout_root(tmp_path, *, deferred=False):
    """Build a v5 root whose frozen geometry is a review-layout artifact."""

    budget = {
        "max_total_cost_usd": 1.0,
        "max_iteration_cost_usd": 0.6,
        "max_total_gpu_minutes": 10.0,
        "max_iteration_gpu_minutes": 5.0,
        "max_revisions": 2,
    }
    service = PipelineService(tmp_path / "domain", ui_schema=5)
    image = Image.new("RGB", (64, 48), (40, 70, 90))
    original = service.artifacts.put(
        "root", "input", _png_bytes(image), role="original", media_type="image/png"
    )
    canonical = service.artifacts.put(
        "root", "canonical", _png_bytes(image), role="canonical", media_type="image/png"
    )
    layout = service.artifacts.put(
        "root",
        "review-layout",
        canonical_json(
            {
                "schema_version": 2,
                "task_id": "root",
                "source_id": original.artifact_id,
                "width": 64,
                "height": 48,
                "elements": [
                    {
                        "element_id": "background",
                        "base_type": "image",
                        "semantic_tags": ["background"],
                        "bbox": [4, 4, 20, 20],
                    }
                ],
                "texts": [],
            }
        ).encode(),
        role="review_layout",
        media_type="application/json",
    )
    selection = None
    if not deferred:
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
                            "target_regions": [{"kind": "element", "element_id": "background"}],
                            "keep_elements": [],
                            "remove_elements": [],
                        }
                    ],
                }
            ).encode(),
            role="selection",
            media_type="application/json",
        )
    request = UIAnalysisRequest(
        input={"kind": "manual", "inputs": [original]},
        output_mode="reconstruct",
        reconstruction_target="scene_background",
        selection_mode="deferred" if deferred else "bound",
        selection_ref=selection,
        budget=budget,
        model_bindings={"canonical_ref": canonical, "layout_ref": layout},
    )
    root = service.ui_plan("root", request)
    if selection is not None:
        from letsaigc.ui_analysis.selection import record_selection

        record_selection(service.artifacts, root.task_id, selection)
    snapshot = service.artifacts.put("root", "model", b'{"sam":"synthetic"}', role="model_snapshot")
    return service, root, canonical, snapshot, layout


def _completed_search_root(tmp_path):
    """Build a completed search-shaped root entirely from local frozen artifacts."""

    from letsaigc.schemas.ui_provider import SearchUIInput, UIInputEntry, UIInputManifest, UISource

    budget = {
        "max_total_cost_usd": 1.0,
        "max_iteration_cost_usd": 0.6,
        "max_total_gpu_minutes": 10.0,
        "max_iteration_gpu_minutes": 5.0,
        "max_revisions": 2,
    }
    service = PipelineService(tmp_path / "domain", ui_schema=5)
    image = Image.new("RGB", (64, 48), (40, 70, 90))
    original = service.artifacts.put(
        "search-root", "search-original", _png_bytes(image), role="original", media_type="image/png"
    )
    provenance = service.artifacts.put(
        "search-root", "search-provenance", b'{"provider":"offline-search-fixture"}', role="provenance"
    )
    source = UISource(
        source_id="search-source",
        original_ref=original,
        provenance_ref=provenance,
        input_entry_ids=["search-entry"],
    )
    manifest = UIInputManifest(
        task_id="search-root",
        status="ready",
        entries=[UIInputEntry(entry_id="search-entry", status="ready", source_id=source.source_id)],
        sources=[source],
    )
    manifest_ref = service.artifacts.put(
        "search-root", "search-manifest", canonical_json(manifest).encode(), role="input_manifest"
    )
    search_input = SearchUIInput(
        query_ref=service.artifacts.put("search-root", "query", b'{"queries":["game ui"]}', role="query"),
        criteria_ref=service.artifacts.put("search-root", "criteria", b'{"candidate_limit":1}', role="criteria"),
        routing_policy_ref=service.artifacts.put(
            "search-root", "routing", b'{"allowed_providers":[]}', role="routing_policy"
        ),
    )
    request = UIAnalysisRequest(
        input=search_input,
        output_mode="reconstruct",
        reconstruction_target="scene_background",
        selection_mode="deferred",
        budget=budget,
    )
    root = service.ui_plan("search-root", request)
    canonical = service.artifacts.put(
        root.task_id, "normalize", _png_bytes(image), role="canonical", media_type="image/png"
    )
    layout = service.artifacts.put(
        root.task_id,
        "layout",
        canonical_json(
            {
                "schema_version": 1,
                "source_id": source.source_id,
                "width": 64,
                "height": 48,
                "canonical_sha256": canonical.sha256,
                "canonical_ref": canonical.model_dump(mode="json"),
                "elements": [
                    {
                        "element_id": "background",
                        "base_type": "image",
                        "semantic_tags": ["background"],
                        "bbox": [4, 4, 20, 20],
                    }
                ],
            }
        ).encode(),
        role="layout",
        media_type="application/json",
    )
    for step_id, refs in (("provide", [manifest_ref]), ("normalize", [canonical]), ("layout", [layout])):
        key = operation_id(root, step_id, 0)
        result = canonical_json({"artifacts": [ref.model_dump(mode="json") for ref in refs]})
        with service.ledger.transaction() as db:
            db.execute(
                """INSERT INTO operations(
                operation_id,task_id,step_id,revision,input_hash,state,reserved_cost,reserved_gpu,
                actual_cost,actual_gpu,result)
                VALUES(?,?,?,?,?,'succeeded',0,0,0,0,?)""",
                (key, root.task_id, step_id, 0, root.fingerprint, result),
            )
    snapshot = service.artifacts.put(root.task_id, "model", b'{"sam":"synthetic"}', role="model_snapshot")
    return service, root, original, canonical, layout, snapshot


def _submitted_segmentation(tmp_path, *, release_failure=False):
    service, _, child = family(tmp_path)
    plan = child("synthetic-segmentation")
    authorize_child(service, plan)
    backend = SyntheticSegmentationBackend(release_failure=release_failure)
    service.backends["ui.segment"] = backend
    binding = EditingExecution(service).binding(plan)
    operation = service.submit_step(plan, binding, Cost(gpu_minutes=1))
    assert operation.state == "submitted"
    service.observe_step(plan, operation.operation_id)
    return service, plan, backend, operation.operation_id


def test_collect_materializes_outputs_before_confirming_provider_release(tmp_path):
    service, plan, backend, operation_id = _submitted_segmentation(tmp_path)

    result = service.collect_step(plan, operation_id)

    assert result.state == "succeeded"
    assert backend.events == [
        ("submit", operation_id),
        ("collect", "synthetic-provider-request"),
        ("release", "synthetic-provider-request"),
    ]
    assert result.result["resource_release"] == {"released": True, "device": "cuda"}
    assert [item["role"] for item in result.result["artifacts"]] == ["segmentation"]
    with service.ledger.transaction() as db:
        assert db.execute(
            "SELECT operation_id FROM resources WHERE operation_id=?", (operation_id,)
        ).fetchone() is None


def test_release_failure_keeps_unknown_usage_and_retry_reuses_provider_request(tmp_path):
    service, plan, backend, operation_id = _submitted_segmentation(tmp_path, release_failure=True)

    with pytest.raises(PipelineError) as caught:
        service.collect_step(plan, operation_id)
    assert caught.value.code == "resource_release_unknown"
    uncertain = service.ledger.get(operation_id)
    assert uncertain.state == "outcome_unknown"
    assert uncertain.provider_request_id == "synthetic-provider-request"
    assert service.ledger.usage(plan.task_id, include_children=True)["unsettled"] == Cost(gpu_minutes=1)
    with service.ledger.transaction() as db:
        assert db.execute(
            "SELECT operation_id FROM resources WHERE operation_id=?", (operation_id,)
        ).fetchone()[0] == operation_id

    backend.release_failure = False
    recovered = service.collect_step(plan, operation_id)
    assert recovered.state == "succeeded"
    assert recovered.provider_request_id == "synthetic-provider-request"
    assert backend.submit_calls == 1
    assert backend.collect_calls == 2
    assert backend.release_calls == 2
    assert service.ledger.usage(plan.task_id, include_children=True)["actual"] == Cost(gpu_minutes=1)
    with service.ledger.transaction() as db:
        assert db.execute(
            "SELECT operation_id FROM resources WHERE operation_id=?", (operation_id,)
        ).fetchone() is None


def test_real_editing_prepare_builds_resize_padded_inpaint_child_from_sam_bundle(tmp_path):
    """A ready SAM bundle becomes a validated inpaint child without provider calls."""

    from letsaigc.agent.storage import AgentStore
    from letsaigc.ui_analysis.selection import TrustedSource, record_selection
    from letsaigc.workflows.compiler import WorkflowCompiler

    budget = {
        "max_total_cost_usd": 1.0,
        "max_iteration_cost_usd": 0.6,
        "max_total_gpu_minutes": 10.0,
        "max_iteration_gpu_minutes": 5.0,
        "max_revisions": 2,
    }
    service = PipelineService(tmp_path / "domain", ui_schema=5)
    original_image = Image.new("RGB", (1600, 1200), (40, 70, 90))
    original = service.artifacts.put(
        "root", "input", _png_bytes(original_image), role="original", media_type="image/png"
    )
    canonical = service.artifacts.put(
        "root", "canonical", _png_bytes(original_image), role="canonical", media_type="image/png"
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
                        "target_regions": [{"kind": "bbox", "xyxy": [0, 0, 20, 20]}],
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
        budget=budget,
    )
    root = service.ui_plan("root", request)
    record_selection(service.artifacts, root.task_id, selection)
    snapshot = service.artifacts.put("root", "model", b'{"sam":"synthetic"}', role="model_snapshot")
    planner = EditingExecution(service)
    prepared = planner.prepare(root, canonical_ref=canonical, model_snapshot_ref=snapshot)
    assert prepared.state == "awaiting_approval"
    assert prepared.child is not None
    segmentation = prepared.child
    service.ledger.consume_approval(approve(service.ledger, segmentation.task_id, segmentation.fingerprint))
    backend = ReadySegmentationBackend(service)
    service.backends["ui.segment"] = backend
    operation = service.submit_step(segmentation, planner.binding(segmentation), Cost(gpu_minutes=1))
    service.observe_step(segmentation, operation.operation_id)
    completed = service.collect_step(segmentation, operation.operation_id)
    assert completed.state == "succeeded"
    assert backend.collect_calls == backend.release_calls == 1

    inpaint_preparation = planner.prepare(root)
    assert inpaint_preparation.state == "awaiting_approval"
    assert inpaint_preparation.child is not None
    inpaint = inpaint_preparation.child
    assert inpaint.workflow_type == "ui_inpaint"
    masked_ref = ArtifactRef.model_validate(inpaint.parameters["request_ref"])
    from letsaigc.schemas.agent import MaskedGenerationPlan

    masked = MaskedGenerationPlan.model_validate_json(service.artifacts.read(masked_ref))
    assert masked.image_mask.crop == (0, 0, 20, 20)
    assert masked.image_mask.width == 24 and masked.image_mask.height == 24
    assert masked.image_mask.width % 8 == masked.image_mask.height % 8 == 0
    assert masked.image_mask.canonical_width == 1600
    assert masked.image_mask.canonical_height == 1200
    assert max(masked.image_mask.width, masked.image_mask.height) <= 1024
    assert all(ref.task_id == inpaint.task_id for ref in inpaint.inputs)

    trusted_original = service.artifacts.put(
        inpaint.task_id,
        "trusted-original",
        service.artifacts.read(masked.image_mask.canonical_ref),
        role="original",
        media_type="image/png",
    )
    source = UISelection.model_validate_json(service.artifacts.read(masked.image_mask.selection_ref)).sources[0]
    trusted_sources = {
        source.source_id: TrustedSource(
            source.source_id,
            trusted_original,
            canonical_ref=masked.image_mask.canonical_ref,
        )
    }
    compiler = WorkflowCompiler(AgentStore(tmp_path / "compiled"))
    artifacts = compiler.validate_masked_artifacts(masked, service.artifacts, trusted_sources)
    assert artifacts["image"].size == (24, 24)
    compiled = compiler.compile(
        masked,
        uploaded_images=["prepared/image.png"],
        uploaded_mask="prepared/mask.png",
        trusted_artifacts=service.artifacts,
        trusted_sources=trusted_sources,
    )
    assert compiled.validation["masked"] is True

    inpaint_approval = approve(service.ledger, inpaint.task_id, inpaint.fingerprint)
    service.ledger.consume_approval(inpaint_approval)
    inpaint_backend = SyntheticInpaintBackend()
    service.backends["ui.inpaint"] = inpaint_backend
    inpaint_operation = service.submit_step(inpaint, planner.binding(inpaint), Cost(gpu_minutes=1))
    service.observe_step(inpaint, inpaint_operation.operation_id)
    completed_inpaint = service.collect_step(inpaint, inpaint_operation.operation_id)
    assert completed_inpaint.state == "succeeded"
    assert [item["role"] for item in completed_inpaint.result["artifacts"]] == ["image"]
    assert completed_inpaint.result["resource_release"]["released"] is True

    terminal = planner.prepare(root)
    assert terminal.state == "succeeded"
    assert terminal.child == inpaint
    assert any(ref.role == "image" for ref in terminal.artifacts)
    operation_count = len(service.ledger.list_operations(inpaint.task_id))
    repeated = planner.prepare(root)
    assert repeated.state == "succeeded"
    assert repeated.child == inpaint
    assert len(service.ledger.list_operations(inpaint.task_id)) == operation_count
    assert inpaint_backend.submit_calls == 1
    assert inpaint_backend.collect_calls == 1
    assert inpaint_backend.release_calls == 1


def test_review_layout_editing_flow_reaches_terminal_planner_state(tmp_path):
    """Confirmed review geometry survives both child scopes and terminal planning."""

    service, root, canonical, snapshot, layout = _review_layout_root(tmp_path)
    planner = EditingExecution(service)
    prepared = planner.prepare(root, canonical_ref=canonical, model_snapshot_ref=snapshot)
    assert prepared.state == "awaiting_approval"
    assert prepared.child is not None
    segmentation = prepared.child
    segmentation_request = UISegmentationRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(segmentation.parameters["request_ref"]))
    )
    assert segmentation_request.prompts[0].box == (4, 4, 20, 20)
    child_selection = UISelection.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(segmentation.parameters["selection_ref"]))
    )
    assert child_selection.sources[0].layout_ref is not None
    assert child_selection.sources[0].layout_ref.role == "review_layout"
    root_request = UIAnalysisRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(root.parameters["request_ref"]))
    )
    assert (
        ArtifactRef.model_validate(segmentation.parameters["root_selection_ref"]).sha256
        == root_request.selection_ref.sha256
    )
    assert json.loads(service.artifacts.read(child_selection.sources[0].layout_ref))["elements"] == json.loads(
        service.artifacts.read(layout)
    )["elements"]

    service.ledger.consume_approval(approve(service.ledger, segmentation.task_id, segmentation.fingerprint))
    segment_backend = ReadySegmentationBackend(service)
    service.backends["ui.segment"] = segment_backend
    segment_operation = service.submit_step(segmentation, planner.binding(segmentation), Cost(gpu_minutes=1))
    service.observe_step(segmentation, segment_operation.operation_id)
    assert service.collect_step(segmentation, segment_operation.operation_id).state == "succeeded"

    inpaint_preparation = planner.prepare(root)
    assert inpaint_preparation.state == "awaiting_approval"
    assert inpaint_preparation.child is not None
    inpaint = inpaint_preparation.child
    service.ledger.consume_approval(approve(service.ledger, inpaint.task_id, inpaint.fingerprint))
    inpaint_backend = SyntheticInpaintBackend()
    service.backends["ui.inpaint"] = inpaint_backend
    inpaint_operation = service.submit_step(inpaint, planner.binding(inpaint), Cost(gpu_minutes=1))
    service.observe_step(inpaint, inpaint_operation.operation_id)
    assert service.collect_step(inpaint, inpaint_operation.operation_id).state == "succeeded"
    terminal = planner.prepare(root)
    assert terminal.state == "succeeded"
    assert terminal.child == inpaint
    assert any(ref.role == "image" for ref in terminal.artifacts)


def test_default_single_review_candidate_is_materialized_without_select(tmp_path):
    """One unambiguous frozen proposal can enter child preparation locally."""

    from letsaigc.ui_analysis.selection import select_candidate, selection_catalog

    service, root, canonical, snapshot, _ = _review_layout_root(tmp_path, deferred=True)
    prepared = EditingExecution(service).prepare(root, canonical_ref=canonical, model_snapshot_ref=snapshot)
    assert prepared.state == "awaiting_approval"
    assert prepared.child is not None
    catalog = selection_catalog(service.artifacts, root.task_id)
    assert catalog is not None
    assert catalog.selection_ref is None
    assert len(catalog.candidates) == 1
    assert catalog.state == "awaiting_approval"
    assert catalog.model_calls == 0
    candidate = catalog.candidates[0]
    assert (
        ArtifactRef.model_validate(prepared.child.parameters["root_selection_ref"]).sha256
        == candidate.selection_ref.sha256
    )

    with service.ledger.selection_change(root.task_id):
        selected = select_candidate(service.artifacts, root.task_id, candidate.candidate_id)
    selected_catalog = selection_catalog(service.artifacts, root.task_id)
    assert selected_catalog is not None and selected_catalog.selection_ref == selected.selection_ref
    replacement = EditingExecution(service).prepare(root, canonical_ref=canonical, model_snapshot_ref=snapshot)
    assert replacement.state == "awaiting_approval"
    assert replacement.child is not None
    assert replacement.child.task_id != prepared.child.task_id
    with service.ledger.transaction() as db:
        assert db.execute(
            "SELECT status FROM ui_child_bindings WHERE task_id=?", (prepared.child.task_id,)
        ).fetchone()[0] == "selection_superseded"


def test_completed_search_root_default_proposal_uses_frozen_outputs_offline(tmp_path):
    """A completed search root can propose editing from local manifest/layout outputs."""

    from letsaigc.ui_analysis.selection import selection_catalog

    service, root, _original, _canonical, _layout, snapshot = _completed_search_root(tmp_path)
    prepared = EditingExecution(service).prepare(root, model_snapshot_ref=snapshot)
    assert prepared.state == "awaiting_approval"
    assert prepared.child is not None
    catalog = selection_catalog(service.artifacts, root.task_id)
    assert catalog is not None
    assert catalog.selection_ref is None
    assert len(catalog.candidates) == 1
    request = UISegmentationRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(prepared.child.parameters["request_ref"]))
    )
    assert request.prompts[0].box == (4, 4, 20, 20)
    assert service.ledger.list_operations(root.task_id)


def test_completed_search_root_accepts_trusted_selection_file_override_offline(tmp_path, monkeypatch):
    """Advanced selection-file input resolves search manifest/layout outputs without providers."""

    from typer.testing import CliRunner

    from letsaigc.cli import app
    from letsaigc.ui_analysis import cli as ui_cli

    service, root, original, _canonical, layout, snapshot = _completed_search_root(tmp_path)
    selection_file = tmp_path / "selection.json"
    selection_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "source_id": "search-source",
                        "original_sha256": original.sha256,
                        "layout_ref": layout.model_dump(mode="json"),
                        "target_regions": [{"kind": "bbox", "xyxy": [8, 8, 24, 24]}],
                        "keep_elements": [],
                        "remove_elements": [],
                    }
                ],
            }
        )
    )
    before = [item.operation_id for item in service.ledger.list_operations(root.task_id)]
    monkeypatch.setattr(ui_cli, "service", lambda: service)
    result = CliRunner().invoke(
        app,
        ["--json", "ui", "select", root.task_id, "--selection", str(selection_file)],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "selection_recorded"
    assert payload["model_calls"] == payload["gpu_calls"] == 0
    assert [item.operation_id for item in service.ledger.list_operations(root.task_id)] == before

    prepared = EditingExecution(service).prepare(root, model_snapshot_ref=snapshot)
    assert prepared.state == "awaiting_approval"
    assert prepared.child is not None
    request = UISegmentationRequest.model_validate_json(
        service.artifacts.read(ArtifactRef.model_validate(prepared.child.parameters["request_ref"]))
    )
    assert request.prompts[0].box == (8, 8, 24, 24)


def test_editing_workflow_local_dispatch_stops_before_unapproved_inpaint(tmp_path, monkeypatch):
    """A segmentation child runs under root ownership, then the root continues for inpaint approval."""

    from temporalio import activity, workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from letsaigc.execution.temporal.activities import PipelineActivities
    from letsaigc.execution.temporal.projection import local_projection
    from letsaigc.execution.temporal.ui_activities import UIActivities
    from letsaigc.execution.temporal.ui_editing_activities import UIEditingActivities
    from letsaigc.execution.temporal.ui_editing_workflow import UIEditingWorkflow
    from letsaigc.execution.temporal.ui_messages import UIEditingWorkflowInput
    from letsaigc.ui_analysis.selection import record_selection

    service, root, child = family(tmp_path)
    plan = child("workflow-segmentation")
    child_approval = approve(service.ledger, plan.task_id, plan.fingerprint)
    backend = SyntheticSegmentationBackend()
    service.backends["ui.segment"] = backend
    local_selection = service.artifacts.read(ArtifactRef.model_validate(plan.parameters["selection_ref"]))
    root_selection = service.artifacts.put(root.task_id, "workflow-selection", local_selection, role="selection")
    record_selection(service.artifacts, root.task_id, root_selection)
    adapters = [
        *PipelineActivities(service).registered(),
        *UIActivities(service).registered(),
        *UIEditingActivities(service).registered(),
    ]
    registered = {activity._Definition.must_from_callable(function).name: function for function in adapters}

    async def execute_activity(name, argument, **kwargs):
        return registered[name](argument)

    async def wait_condition(condition, timeout=None):
        assert condition(), "Unexpected wait at the local activity boundary"

    monkeypatch.setattr(activity, "heartbeat", lambda *args: None)
    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(
        workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id=root.task_id, run_id="run-editing", is_continue_as_new_suggested=lambda: False
        ),
    )
    monkeypatch.setattr(workflow, "all_handlers_finished", lambda: True)
    monkeypatch.setattr(workflow, "now", lambda: datetime.now(UTC))
    monkeypatch.setattr(workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(workflow, "sleep", lambda *_args, **_kwargs: asyncio.sleep(0))

    class Continued(BaseException):
        def __init__(self, argument):
            self.argument = argument

    monkeypatch.setattr(workflow, "continue_as_new", lambda argument: (_ for _ in ()).throw(Continued(argument)))

    async def run():
        SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(UIEditingWorkflow))
        argument = UIEditingWorkflowInput(
            plan=root,
            approved=True,
            phase_index=7,
            child=plan,
            initial_child_approval=child_approval,
            poll_seconds=0.01,
            observations_per_run=1,
        )
        try:
            await UIEditingWorkflow().run(argument)
        except Continued as event:
            return event.argument

    continuation = asyncio.run(run())

    assert continuation.plan.task_id == root.task_id
    assert continuation.run.task_id == root.task_id
    assert continuation.run.steps[0].step_id == "segment"
    assert continuation.run.steps[0].state == "succeeded"
    assert continuation.child is None
    assert continuation.child_approved is False
    assert continuation.initial_child_approval is None
    assert backend.submit_calls == backend.inspect_calls == backend.collect_calls == backend.release_calls == 1
    assert backend.events == [
        ("submit", continuation.run.steps[0].operation_id),
        ("collect", "synthetic-provider-request"),
        ("release", "synthetic-provider-request"),
    ]
    operation = service.ledger.get(continuation.run.steps[0].operation_id)
    assert operation.task_id == plan.task_id
    assert operation.state == "succeeded"
    assert operation.result["resource_release"] == {"released": True, "device": "cuda"}
    with service.ledger.transaction() as db:
        approval_row = db.execute(
            "SELECT consumed FROM approvals WHERE request_id=?", (child_approval.request_id,)
        ).fetchone()
        assert approval_row["consumed"] == 1
        assert db.execute(
            "SELECT operation_id FROM resources WHERE operation_id=?", (operation.operation_id,)
        ).fetchone() is None
    assert local_projection(service, root.task_id)["state"] == "running"


def test_edit_cancel_releases_prepared_child_and_closes_root_gate(tmp_path, monkeypatch):
    """Cancellation closes the root gate and releases child reservations atomically."""

    from temporalio import activity

    from letsaigc.execution.temporal.ui_editing_activities import UIEditingActivities
    from letsaigc.execution.temporal.ui_messages import UIEditingActivityInput
    from letsaigc.ui_analysis.selection import record_selection

    service, root, child = family(tmp_path)
    plan = child("cancel-prepared")
    approval = approve(service.ledger, plan.task_id, plan.fingerprint)
    service.ledger.consume_approval(approval)
    operation = reserve_child(service, plan)
    local_selection = service.artifacts.read(ArtifactRef.model_validate(plan.parameters["selection_ref"]))
    root_selection = service.artifacts.put(root.task_id, "cancel-selection", local_selection, role="selection")
    record_selection(service.artifacts, root.task_id, root_selection)
    monkeypatch.setattr(activity, "heartbeat", lambda *args: None)

    argument = UIEditingActivityInput(
        task_id=root.task_id,
        plan_fingerprint=root.fingerprint,
        child=plan,
    )
    assert UIEditingActivities(service).edit_cancel(argument) is True

    assert service.ledger.get(operation.operation_id).state == "failed"
    assert service.ledger.get(operation.operation_id).result["cancelled_before_submission"] is True
    assert service.ledger.usage(root.task_id, include_children=True)["reserved"] == Cost()
    with service.ledger.transaction() as db:
        assert db.execute(
            "SELECT cancelled FROM tasks WHERE task_id=?", (root.task_id,)
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT submission_gate FROM ui_budget_groups WHERE root_task_id=?", (root.task_id,)
        ).fetchone()[0] == "closed"
        assert db.execute(
            "SELECT operation_id FROM resources WHERE operation_id=?", (operation.operation_id,)
        ).fetchone() is None


def test_editing_workflow_turns_active_time_limit_into_terminal_failure(tmp_path, monkeypatch):
    """The editing coordinator uses the same cleanup path for an expired active limit."""

    from temporalio import activity, workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from letsaigc.execution.temporal.activities import PipelineActivities
    from letsaigc.execution.temporal.projection import local_projection
    from letsaigc.execution.temporal.ui_activities import UIActivities
    from letsaigc.execution.temporal.ui_editing_activities import UIEditingActivities
    from letsaigc.execution.temporal.ui_editing_workflow import UIEditingWorkflow
    from letsaigc.execution.temporal.ui_messages import UIEditingWorkflowInput

    service, root, child = family(tmp_path)
    plan = child("active-limit")
    adapters = [
        *PipelineActivities(service).registered(),
        *UIActivities(service).registered(),
        *UIEditingActivities(service).registered(),
    ]
    registered = {activity._Definition.must_from_callable(function).name: function for function in adapters}

    async def execute_activity(name, argument, **kwargs):
        return registered[name](argument)

    monkeypatch.setattr(activity, "heartbeat", lambda *args: None)
    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(
        workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id=root.task_id, run_id="run-active-limit", is_continue_as_new_suggested=lambda: False
        ),
    )
    monkeypatch.setattr(workflow, "all_handlers_finished", lambda: True)
    monkeypatch.setattr(workflow, "now", lambda: datetime.now(UTC))

    async def run():
        SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(UIEditingWorkflow))
        argument = UIEditingWorkflowInput(
            plan=root,
            approved=True,
            phase_index=7,
            child=plan,
            active_seconds=1,
            active_limit_seconds=1,
            poll_seconds=0.01,
        )
        return await UIEditingWorkflow().run(argument)

    result = asyncio.run(run())
    assert result.task_id == root.task_id
    assert result.state == "failed"
    assert result.stop_reason == "active_time_limit"
    assert local_projection(service, root.task_id)["state"] == "failed"
    with service.ledger.transaction() as db:
        assert db.execute(
            "SELECT cancelled FROM tasks WHERE task_id=?", (root.task_id,)
        ).fetchone()[0] == 1


def test_edit_approval_rejects_root_receipt_and_superseded_child(tmp_path, monkeypatch):
    from temporalio import activity
    from temporalio.exceptions import ApplicationError

    from letsaigc.execution.temporal.ui_editing_activities import UIEditingActivities
    from letsaigc.execution.temporal.ui_messages import UIEditingActivityInput
    from letsaigc.schemas.pipeline import ApprovalRequest
    from letsaigc.ui_analysis.selection import record_selection

    service, root, child = family(tmp_path)
    plan = child("approval-scope")
    child_approval = approve(service.ledger, plan.task_id, plan.fingerprint)
    local_selection = service.artifacts.read(ArtifactRef.model_validate(plan.parameters["selection_ref"]))
    root_selection = service.artifacts.put(root.task_id, "approval-selection", local_selection, role="selection")
    record_selection(service.artifacts, root.task_id, root_selection)
    activities = UIEditingActivities(service)
    monkeypatch.setattr(activity, "heartbeat", lambda *args: None)
    root_receipt = ApprovalRequest(
        request_id="root-receipt",
        task_id=root.task_id,
        plan_fingerprint=root.fingerprint,
        decision="approve",
    )
    argument = UIEditingActivityInput(
        task_id=root.task_id,
        plan_fingerprint=root.fingerprint,
        child=plan,
        approval=root_receipt,
    )
    with pytest.raises(ApplicationError) as caught:
        activities.edit_approval(argument)
    assert caught.value.type == "approval_mismatch"

    with service.ledger.selection_change(root.task_id):
        pass
    stale = argument.model_copy(update={"approval": child_approval})
    with pytest.raises(ApplicationError) as caught:
        activities.edit_approval(stale)
    assert caught.value.type == "selection_superseded"


@pytest.mark.runtime
@pytest.mark.skipif(
    not os.getenv("LETSAIGC_TEMPORAL_TEST_CLI"),
    reason="Set LETSAIGC_TEMPORAL_TEST_CLI to the verified local Temporal CLI",
)
def test_real_temporal_editing_worker_restarts_after_child_acceptance(tmp_path):
    """Exercise root-owned child approvals and accepted-request recovery on a real worker.

    The worker subprocess uses a deterministic ``EditingExecution.prepare`` stub
    to isolate Temporal boundaries from planner iteration.  Domain-level planner
    and CPU mask behavior are covered by the unit tests; this test proves that
    segmentation and inpaint still use separate approvals after Continue-As-New.
    """

    import signal
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path

    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.testing import WorkflowEnvironment

    from letsaigc.execution.temporal.projection import local_projection
    from letsaigc.execution.temporal.ui_editing_workflow import UIEditingWorkflow
    from letsaigc.execution.temporal.ui_messages import UIEditingWorkflowInput
    from letsaigc.schemas.pipeline import PipelineRun, PipelineState, operation_id
    from letsaigc.ui_analysis.selection import record_selection

    service, root, child = family(tmp_path / "domain")
    segmentation = child("real-temporal-segmentation")
    inpaint = child("real-temporal-inpaint", purpose="inpaint")
    segmentation_approval = approve(service.ledger, segmentation.task_id, segmentation.fingerprint)
    local_selection = service.artifacts.read(ArtifactRef.model_validate(segmentation.parameters["selection_ref"]))
    root_selection = service.artifacts.put(root.task_id, "real-temporal-selection", local_selection, role="selection")
    record_selection(service.artifacts, root.task_id, root_selection)

    worker_source = r'''
import asyncio
import io
import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image
from temporalio.worker import Worker

from letsaigc.execution.temporal.activities import PipelineActivities
from letsaigc.execution.temporal.client import connect
from letsaigc.execution.temporal.config import TemporalConfig
from letsaigc.execution.temporal.ui_activities import UIActivities
from letsaigc.execution.temporal.ui_editing_activities import UIEditingActivities
from letsaigc.execution.temporal.ui_editing_workflow import UIEditingWorkflow
from letsaigc.pipelines.contracts import Capability, Submission
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import Cost, ArtifactRef
from letsaigc.schemas.ui import UIObservation
from letsaigc.ui_analysis.editing import EditingExecution, EditingPreparation


class FakeEditingBackend:
    def __init__(self, service, provider_db, *, capability, output_role, prefix, crash=False):
        self.service = service
        self.provider_db = provider_db
        self.capability = Capability(id=capability, idempotent_submission=True, resource="local-gpu")
        self.output_role = output_role
        self.prefix = prefix
        self.crash = crash
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.provider_db) as db:
            db.execute("CREATE TABLE IF NOT EXISTS events(kind TEXT, operation_id TEXT, request_id TEXT)")

    def _event(self, kind, operation_id, request_id):
        with sqlite3.connect(self.provider_db) as db:
            db.execute("INSERT INTO events VALUES(?,?,?)", (kind, operation_id, request_id))

    def _request_id(self, operation_id):
        return self.prefix + operation_id

    def submit(self, operation_id, arguments):
        request_id = self._request_id(operation_id)
        self._event("submit", operation_id, request_id)
        if self.crash:
            marker = self.provider_db.with_suffix(".crashed")
            if not marker.exists():
                marker.write_text("accepted", encoding="utf-8")
                os._exit(23)
        return Submission(request_id=request_id, metadata={"request_id": request_id})

    def recover(self, operation_id):
        request_id = self._request_id(operation_id)
        self._event("recover", operation_id, request_id)
        return Submission(request_id=request_id, metadata={"request_id": request_id})

    def inspect(self, submission):
        return UIObservation(state="succeeded", actual=Cost(gpu_minutes=1))

    def collect(self, submission):
        if self.output_role == "image":
            stream = io.BytesIO()
            Image.new("RGB", (64, 48), (18, 28, 38)).save(stream, format="PNG")
            data = stream.getvalue()
        else:
            data = json.dumps({"synthetic": True}).encode()
        return [(self.output_role, data, "image/png" if self.output_role == "image" else "application/json")]

    def release_operation(self, submission):
        self._event("release", submission.request_id.removeprefix(self.prefix), submission.request_id)
        return {"released": True, "device": "synthetic-local"}


def deterministic_prepare(self, root, **kwargs):
    with self.service.ledger.transaction() as db:
        rows = db.execute(
            "SELECT task_id,purpose,selection_revision,source_ids,status "
            "FROM ui_child_bindings WHERE root_task_id=? ORDER BY purpose,task_id",
            (root.task_id,),
        ).fetchall()
    for purpose in ("segmentation", "inpaint"):
        row = next((item for item in rows if item["purpose"] == purpose), None)
        if row is None:
            continue
        child = self.service.ledger.plan(row["task_id"])
        operations = self.service.ledger.list_operations(child.task_id)
        if any(item.state in {"submitting", "submitted", "running", "outcome_unknown"} for item in operations):
            state = "awaiting_reconciliation"
        elif any(item.state == "succeeded" for item in operations):
            continue
        else:
            state = "awaiting_approval"
        selection_ref = ArtifactRef.model_validate(child.parameters["selection_ref"])
        return EditingPreparation(
            root_task_id=root.task_id,
            state=state,
            child=child,
            selection_ref=selection_ref,
            selection_revision=int(row["selection_revision"]),
            source_id=json.loads(row["source_ids"])[0],
        )
    return EditingPreparation(root_task_id=root.task_id, state="succeeded")


async def main():
    address, data_root, mode = sys.argv[1:]
    root = Path(data_root)
    service = PipelineService(
        root,
        backends={
            "ui.segment": FakeEditingBackend(
                service=None, provider_db=root / "providers.sqlite", capability="ui.segment",
                output_role="segmentation", prefix="sam-", crash=mode == "crash",
            ),
            "ui.inpaint": FakeEditingBackend(
                service=None, provider_db=root / "providers.sqlite", capability="ui.inpaint",
                output_role="image", prefix="comfy-",
            ),
        },
    )
    for backend in service.backends.values():
        backend.service = service
    EditingExecution.prepare = deterministic_prepare
    client = await connect(TemporalConfig(address=address, task_queue="test-ui-editing"))
    activities = [
        *PipelineActivities(service).registered(),
        *UIActivities(service).registered(),
        *UIEditingActivities(service).registered(),
    ]
    with ThreadPoolExecutor(4) as executor:
        async with Worker(
            client,
            task_queue="test-ui-editing",
            workflows=[UIEditingWorkflow],
            activities=activities,
            activity_executor=executor,
            build_id="test-ui-editing-" + mode,
        ):
            await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
'''
    worker_path = tmp_path / "editing_worker.py"
    worker_path.write_text(worker_source, encoding="utf-8")
    cli = os.environ["LETSAIGC_TEMPORAL_TEST_CLI"]
    worker_env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
    }

    async def wait_until(predicate, timeout=45):
        async def loop():
            while True:
                value = predicate()
                if value:
                    return value
                await asyncio.sleep(0.05)

        return await asyncio.wait_for(loop(), timeout)

    async def scenario():
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=cli,
            dev_server_database_filename=str(tmp_path / "temporal.sqlite"),
            data_converter=pydantic_data_converter,
            ui=False,
            dev_server_log_level="error",
        ) as environment:
            address = environment.client.service_client.config.target_host
            log_path = tmp_path / "worker.log"
            with log_path.open("wb") as log:
                def launch(mode):
                    return subprocess.Popen(
                        [sys.executable, str(worker_path), address, str(service.root), mode],
                        stdout=log,
                        stderr=log,
                        env=worker_env,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )

                process = launch("crash")
                try:
                    handle = await environment.client.start_workflow(
                        UIEditingWorkflow.run,
                        UIEditingWorkflowInput(
                            plan=root,
                            approved=True,
                            phase_index=7,
                            child=segmentation,
                            initial_child_approval=segmentation_approval,
                            poll_seconds=0.05,
                            activity_timeout_seconds=5,
                            observations_per_run=1,
                        ),
                        id=root.task_id,
                        task_queue="test-ui-editing",
                        result_type=PipelineRun,
                    )
                    await wait_until(lambda: process.poll() == 23)
                    operation_key = operation_id(segmentation, "segment", 0)
                    operation = service.ledger.get(operation_key)
                    assert operation.state == "submitting"
                    assert (tmp_path / "domain" / "providers.crashed").read_text(encoding="utf-8") == "accepted"
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)

                process = launch("recover")
                try:
                    await wait_until(lambda: service.ledger.get(operation_key).state == "outcome_unknown")
                    await handle.execute_update("reconcile", id="reconcile-segmentation", result_type=str)
                    await wait_until(lambda: service.ledger.get(operation_key).state == "succeeded")
                    await wait_until(
                        lambda: (service.ledger.get(operation_key).state == "succeeded"
                                 and (service.ledger.plan(inpaint.task_id) == inpaint))
                    )

                    async def inpaint_gate():
                        while True:
                            current = await handle.query("state", result_type=PipelineRun | None)
                            if (
                                current is not None
                                and current.state == PipelineState.awaiting_approval
                                and current.steps
                                and current.steps[-1].step_id == "segment"
                            ):
                                return
                            await asyncio.sleep(0.05)

                    await asyncio.wait_for(inpaint_gate(), 45)
                    inpaint_approval = approve(service.ledger, inpaint.task_id, inpaint.fingerprint)
                    await handle.execute_update("approval", inpaint_approval, id=inpaint_approval.request_id,
                                                result_type=str)
                    result = await asyncio.wait_for(handle.result(), 60)
                finally:
                    if process.poll() is None:
                        process.send_signal(signal.SIGTERM)
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)

            assert result.task_id == root.task_id
            assert result.state == PipelineState.succeeded
            assert [step.step_id for step in result.steps] == ["segment", "inpaint"]
            assert all(step.state == "succeeded" for step in result.steps)
            assert result.temporal_run_id != handle.first_execution_run_id
            segment_operation = service.ledger.get(operation_key)
            inpaint_operation = service.ledger.get(operation_id(inpaint, "inpaint", 0))
            assert segment_operation.provider_request_id == "sam-" + operation_key
            assert inpaint_operation.provider_request_id == "comfy-" + inpaint_operation.operation_id
            assert local_projection(service, root.task_id)["state"] == "succeeded"
            with sqlite3.connect(tmp_path / "domain" / "providers.sqlite") as db:
                assert db.execute(
                    "SELECT COUNT(*) FROM events WHERE kind='submit' AND operation_id=?", (operation_key,)
                ).fetchone()[0] == 1
                assert db.execute(
                    "SELECT COUNT(*) FROM events WHERE kind='recover' AND operation_id=?", (operation_key,)
                ).fetchone()[0] >= 1
                assert db.execute(
                    "SELECT COUNT(*) FROM events WHERE kind='submit' AND operation_id=?",
                    (inpaint_operation.operation_id,),
                ).fetchone()[0] == 1

    asyncio.run(scenario())
