"""A child-budget upgrade preserves already-confirmed review inputs."""

from io import BytesIO

from PIL import Image

from letsaigc.pipelines.migrations import migrate_ui_ledger
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas.ui_review import ReviewConfirm, ReviewDocument, ReviewPatch
from letsaigc.ui_analysis.review import ReviewRepository


def test_v4_confirmed_review_survives_v5_and_can_save_new_draft(tmp_path):
    service = PipelineService(tmp_path, ui_schema=4)
    plan = service.smoke_plan("historic-review")
    stream = BytesIO()
    Image.new("RGB", (5, 5)).save(stream, format="PNG")
    canonical = service.artifacts.put(plan.task_id, "input", stream.getvalue(), role="canonical")
    repo = ReviewRepository(service.ledger, service.artifacts)
    repo.initialize(ReviewDocument(task_id=plan.task_id, source_id="image", width=5, height=5),
                    {"canonical_ref": canonical.model_dump(mode="json")})
    repo.confirm(plan.task_id, ReviewConfirm(request_id="old-confirm", draft_revision=0))
    frozen = repo.binding(plan.task_id, 0)
    old_layout = service.artifacts.read(frozen.layout_ref)
    migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    reopened = PipelineService(tmp_path)
    upgraded = ReviewRepository(reopened.ledger, reopened.artifacts)
    assert upgraded.binding(plan.task_id, 0) == frozen
    upgraded.save(plan.task_id, ReviewPatch(request_id="new-draft", base_confirmed_revision=0, actions=[]))
    assert upgraded.binding(plan.task_id, 0) == frozen
    assert reopened.artifacts.read(frozen.layout_ref) == old_layout
    assert reopened.ledger.plan(plan.task_id) == plan
    assert reopened.ledger.list_operations(plan.task_id) == []
