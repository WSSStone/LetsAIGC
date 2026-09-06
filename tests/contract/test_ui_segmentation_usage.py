"""Provider completion and mask quality must not erase measured GPU usage."""

from types import SimpleNamespace

import pytest
from test_ui_segmentation import _sam_job

from letsaigc.vision.service import VisionApplication


@pytest.mark.parametrize("quality", ["ready", "partial", "unknown"])
def test_completed_model_work_is_metered_even_when_quality_is_unknown(tmp_path, ui_store, quality):
    job = _sam_job(ui_store)
    app = VisionApplication(
        tmp_path / "provider", ui_store, token="test-token", signing_key="t" * 32,
        model_digest=job.model_digest, capability="segmentation",
        engine=SimpleNamespace(segment=lambda *_: {"status": quality, "items": []}),
    )
    receipt = app.jobs.accept(job)
    app._run(job, receipt["request_id"])
    state = app.jobs.job(receipt["request_id"], task_id=job.task_id)
    assert state["state"] == "succeeded"
    assert state["actual"] is not None
    assert state["actual"]["gpu_minutes"] > 0
    assert state["actual"]["cost_usd"] == 0


def test_interrupted_or_failed_model_work_does_not_get_fabricated_zero_usage(tmp_path, ui_store):
    job = _sam_job(ui_store)

    def fail(*_):
        raise RuntimeError("provider execution failed")

    app = VisionApplication(
        tmp_path / "provider", ui_store, token="test-token", signing_key="t" * 32,
        model_digest=job.model_digest, capability="segmentation", engine=SimpleNamespace(segment=fail),
    )
    receipt = app.jobs.accept(job)
    app._run(job, receipt["request_id"])
    state = app.jobs.job(receipt["request_id"], task_id=job.task_id)
    assert state["state"] == "failed" and state["actual"] is None
