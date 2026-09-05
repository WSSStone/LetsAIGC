import json

import pytest

from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.service import PipelineService
from letsaigc.tracking.manifest import create_ui_manifest


def test_manifest_preserves_references_and_quality_pending_without_copying_private_content(tmp_path):
    service = PipelineService(tmp_path)
    plan = service.smoke_plan("manifest-test")
    ref = service.artifacts.put(plan.task_id, "source", b'{"text":"C:/private?token=hidden"}', role="texts")
    output = create_ui_manifest(service, plan, [ref], evidence_kind="offline")
    result = json.loads(service.artifacts.read(output))
    assert result["quality_status"] == "pending"
    assert result["evidence_kind"] == "offline"
    assert result["outputs"][0]["sha256"] == ref.sha256
    assert "hidden" not in json.dumps(result)
    other = service.artifacts.put("other-task", "input", b"{}", role="texts")
    with pytest.raises(PipelineError):
        create_ui_manifest(service, plan, [other], evidence_kind="offline")


def test_many_ocr_operations_use_a_reference_index_instead_of_oversized_transport(tmp_path, monkeypatch):
    from letsaigc.schemas.pipeline import OperationRecord

    service = PipelineService(tmp_path)
    plan = service.smoke_plan("many-tiles")
    operations = [
        OperationRecord(
            operation_id=f"op-{index}",
            task_id=plan.task_id,
            step_id=f"ocr.{index}",
            revision=0,
            input_hash=plan.fingerprint,
            state="succeeded",
            result={"detail": "x" * 1500},
        )
        for index in range(64)
    ]
    monkeypatch.setattr(service.ledger, "list_operations", lambda task_id: operations)
    output = create_ui_manifest(service, plan, [], evidence_kind="offline")
    result = json.loads(service.artifacts.read(output))
    assert output.size_bytes < 65536
    from letsaigc.schemas.pipeline import ArtifactRef

    index = json.loads(service.artifacts.read(ArtifactRef.model_validate(result["operations_ref"])))
    assert len(index) == 64
