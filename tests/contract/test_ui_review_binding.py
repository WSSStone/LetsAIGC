import json

import pytest

from letsaigc.pipelines.errors import PipelineError
from letsaigc.schemas.ui_review import ReviewConfirm, ReviewDocument


@pytest.mark.parametrize("schema_version", [4, 5])
def test_binding_requires_confirmed_specific_version_and_verified_hash(tmp_path, schema_version):
    from letsaigc.pipelines.service import PipelineService
    from letsaigc.ui_analysis.review import ReviewRepository

    service = PipelineService(tmp_path, ui_schema=schema_version)
    service.smoke_plan("bound")
    repo = ReviewRepository(service.ledger, service.artifacts)
    repo.initialize(ReviewDocument(task_id="bound", source_id="image", width=5, height=5), {})
    with pytest.raises(PipelineError):
        repo.binding("bound", 0)


@pytest.mark.parametrize("schema_version", [4, 5])
def test_confirmed_binding_does_not_follow_new_head(tmp_path, schema_version):
    from io import BytesIO

    from PIL import Image

    from letsaigc.pipelines.service import PipelineService
    from letsaigc.schemas.ui_review import ReviewPatch
    from letsaigc.ui_analysis.review import ReviewRepository

    service = PipelineService(tmp_path, ui_schema=schema_version)
    service.smoke_plan("bound")
    stream = BytesIO()
    Image.new("RGB", (5, 5)).save(stream, format="PNG")
    canonical = service.artifacts.put("bound", "base", stream.getvalue(), role="canonical", media_type="image/png")
    repo = ReviewRepository(service.ledger, service.artifacts)
    repo.initialize(
        ReviewDocument(task_id="bound", source_id="image", width=5, height=5),
        {"canonical_ref": canonical.model_dump(mode="json")},
    )
    repo.confirm("bound", ReviewConfirm(request_id="c0", draft_revision=0))
    binding = repo.binding("bound", 0)
    repo.save("bound", ReviewPatch(request_id="next", base_confirmed_revision=0, actions=[]))
    assert repo.binding("bound", 0) == binding
    assert binding.evaluation_lane == "human_assisted"
    with pytest.raises(ValueError):
        type(binding).model_validate({**binding.model_dump(), "evaluation_lane": "ground_truth"})
    with pytest.raises(ValueError):
        type(binding).model_validate({**binding.model_dump(), "task_id": "foreign"})
    raw = json.loads(repo.store.read(binding.layout_ref))
    assert raw["schema_version"] == 2
    repo.store.resolve(binding.layout_ref).write_bytes(b"changed")
    with pytest.raises(PipelineError):
        repo.binding("bound", 0)


@pytest.mark.parametrize("schema_version", [4, 5])
def test_preview_export_verifies_confirmed_files_without_overwrite(tmp_path, schema_version):
    from io import BytesIO

    from PIL import Image

    from letsaigc.pipelines.service import PipelineService
    from letsaigc.ui_analysis.review import ReviewRepository
    from letsaigc.ui_analysis.review_projection import export_review

    service = PipelineService(tmp_path / "runtime", ui_schema=schema_version)
    service.smoke_plan("export-review")
    stream = BytesIO()
    Image.new("RGB", (5, 5)).save(stream, format="PNG")
    ref = service.artifacts.put("export-review", "base", stream.getvalue(), role="canonical", media_type="image/png")
    repo = ReviewRepository(service.ledger, service.artifacts)
    repo.initialize(
        ReviewDocument(task_id="export-review", source_id="source", width=5, height=5),
        {"canonical_ref": ref.model_dump(mode="json")},
    )
    repo.confirm("export-review", ReviewConfirm(request_id="confirm", draft_revision=0))
    binding = export_review(repo, "export-review", 0, tmp_path / "export")
    assert (tmp_path / "export/layout.json").read_bytes() == repo.store.read(binding.layout_ref)
    assert json.loads((tmp_path / "export/manifest.json").read_text())["production_export_approved"] is False
    with pytest.raises(FileExistsError):
        export_review(repo, "export-review", 0, tmp_path / "export")
