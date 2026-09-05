"""Single-image wiring with recorded fake model boundaries; no live inference."""

import asyncio
import json
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.contracts import Capability, Submission
from letsaigc.pipelines.migrations import migrate_ui_ledger
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.pipeline import Cost
from letsaigc.schemas.ui import ImageView, UIAnalysisRequest, UIObservation
from letsaigc.ui_analysis.execution import UIExecution
from letsaigc.ui_providers.intake import UIIntake
from letsaigc.vision.ocr import normalize_ocr_result


class ModelBackend:
    def __init__(self, capability, store):
        self.capability = Capability(id=capability)
        self.store = store
        self.calls = 0
        self.outputs = {}

    def submit(self, operation_id, arguments):
        self.calls += 1
        refs = arguments["binding"]["inputs"]
        from letsaigc.schemas.pipeline import ArtifactRef

        view_ref = next(ArtifactRef.model_validate(ref) for ref in refs if ref["role"] == "view_manifest")
        view = ImageView.model_validate_json(self.store.read(view_ref))
        if self.capability.id == "ui.ocr":
            result = normalize_ocr_result(
                {"rec_texts": [], "rec_scores": [], "rec_polys": [], "dt_polys": []},
                view,
                model_id="fake-ocr",
                model_version="test",
            )
            role = "texts"
        else:
            result = {
                "view": view.model_dump(mode="json"),
                "output": {
                    "observations": [],
                    "hypotheses": [],
                    "elements": [
                        {
                            "id": "panel",
                            "kind": "panel",
                            "bbox": [2, 3, 30, 20],
                            "parent_id": None,
                            "evidence_ids": [view.view_id],
                        }
                    ],
                    "text_links": [],
                    "occlusions": [],
                    "correction_suggestions": [],
                    "revision_proposals": [],
                },
            }
            role = "analysis"
        self.outputs[operation_id] = [(role, json.dumps(result).encode(), "application/json")]
        return Submission(request_id=operation_id)

    def inspect(self, submission):
        return UIObservation(state="succeeded", actual=Cost())

    def collect(self, submission):
        return self.outputs[submission.request_id]


def test_manual_produce_outputs_and_resume_after_ocr_without_another_model_call(tmp_path):
    service = PipelineService(tmp_path / "service")
    migrate_ui_ledger(service.ledger.path, writers_stopped=True)
    data = BytesIO()
    Image.new("RGB", (48, 32), (40, 70, 90)).save(data, format="PNG")
    path = tmp_path / "input.png"
    path.write_bytes(data.getvalue())
    intake = UIIntake(service.artifacts)
    imported = intake.import_images([str(path)])
    request = UIAnalysisRequest(
        input=intake.rebind(imported, "ui-wiring", allowed_scopes={imported.task_id}),
        budget={
            "max_total_cost_usd": 1,
            "max_iteration_cost_usd": 0.25,
            "max_total_gpu_minutes": 0,
            "max_iteration_gpu_minutes": 0,
            "max_revisions": 2,
        },
    )
    plan = service.ui_plan("ui-wiring", request)
    service.ledger.consume_approval(approve(service.ledger, plan.task_id, plan.fingerprint))
    ocr = ModelBackend("ui.ocr", service.artifacts)
    analyzer = ModelBackend("ui.analyze", service.artifacts)
    service.backends.update({"ui.ocr": ocr, "ui.analyze": analyzer})
    runner = UIExecution(service, evidence_kind="offline")
    for phase in ("provide", "normalize", "ocr"):
        item = runner.prepare(plan, phase, 0)
        operation = service.submit_step(plan, item.binding, item.reservation)
        service.observe_step(plan, operation.operation_id)
        service.collect_step(plan, operation.operation_id)
    assert ocr.calls == 1
    runner = UIExecution(service, evidence_kind="offline")  # Representative coordinator interruption.
    for phase in ("provide", "normalize", "ocr", "analyze", "layout", "crop", "project"):
        item = runner.prepare(plan, phase, 0)
        operation = service.submit_step(plan, item.binding, item.reservation)
        service.observe_step(plan, operation.operation_id)
        service.collect_step(plan, operation.operation_id)
    assert ocr.calls == 1 and analyzer.calls == 1
    outputs = runner.outputs(plan)
    assert {"sources", "canonical", "transforms", "texts", "layout", "overlay", "asset_index", "manifest"} <= {
        ref.role for ref in outputs
    }
    manifest = json.loads(service.artifacts.read(next(ref for ref in outputs if ref.role == "manifest")))
    assert manifest["quality_status"] == "pending" and manifest["evidence_kind"] == "offline"


def test_temporal_ui_registration_and_continue_as_new_with_local_activity_dispatch(tmp_path, monkeypatch):
    """SDK sandbox plus real UI orchestration with fake transport/model boundaries."""
    from temporalio import activity, workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from letsaigc.execution.temporal.activities import PipelineActivities
    from letsaigc.execution.temporal.projection import local_projection
    from letsaigc.execution.temporal.ui_activities import UIActivities
    from letsaigc.execution.temporal.ui_messages import UIWorkflowInput
    from letsaigc.execution.temporal.ui_workflow import UIAnalysisWorkflow
    from letsaigc.schemas.pipeline import validate_payload

    service = PipelineService(tmp_path / "service", ui_schema=True)
    path = tmp_path / "input.png"
    Image.new("RGB", (48, 32)).save(path)
    intake = UIIntake(service.artifacts)
    ref = intake.import_images([str(path)])
    request = UIAnalysisRequest(
        input=intake.rebind(ref, "ui-temporal", allowed_scopes={ref.task_id}),
        budget={
            "max_total_cost_usd": 1,
            "max_iteration_cost_usd": 0.25,
            "max_total_gpu_minutes": 0,
            "max_iteration_gpu_minutes": 0,
            "max_revisions": 2,
        },
    )
    plan = service.ui_plan("ui-temporal", request)
    ocr, analyzer = (ModelBackend("ui." + name, service.artifacts) for name in ("ocr", "analyze"))
    service.backends.update({"ui.ocr": ocr, "ui.analyze": analyzer})
    adapters = [*PipelineActivities(service).registered(), *UIActivities(service).registered()]
    registered = {activity._Definition.must_from_callable(function).name: function for function in adapters}

    class Continued(BaseException):
        def __init__(self, argument):
            self.argument = argument

    def continue_as_new(argument):
        validate_payload(argument)
        raise Continued(argument)

    async def execute_activity(name, argument, **kwargs):
        return registered[name](argument)

    async def wait_condition(condition):
        assert condition(), "Unexpected wait at the fake transport boundary"

    monkeypatch.setattr(activity, "heartbeat", lambda *args: None)
    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(
        workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id=plan.task_id, run_id="run-test", is_continue_as_new_suggested=lambda: False
        ),
    )
    monkeypatch.setattr(workflow, "all_handlers_finished", lambda: True)
    from datetime import UTC, datetime

    monkeypatch.setattr(workflow, "now", lambda: datetime.now(UTC))
    monkeypatch.setattr(workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(workflow, "continue_as_new", continue_as_new)

    async def run():
        SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(UIAnalysisWorkflow))
        argument = UIWorkflowInput(
            plan=plan, initial_approval=approve(service.ledger, plan.task_id, plan.fingerprint), observations_per_run=3
        )
        continuations = 0
        while True:
            try:
                return await UIAnalysisWorkflow().run(argument), continuations
            except Continued as event:
                argument = event.argument
                continuations += 1
                assert continuations <= 3

    result, count = asyncio.run(run())
    assert result.state.value == "succeeded" and count == 2
    assert ocr.calls == analyzer.calls == 1
    assert len(result.steps) == 7
    assert local_projection(service, plan.task_id)["state"] == "succeeded"
