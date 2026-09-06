"""Verify the real worker interruption reused its registered OCR request."""

import json

import pytest

pytestmark = pytest.mark.ui_live


def test_registered_ocr_request_survives_worker_restart(ui_live_evidence):
    root, runs = ui_live_evidence
    before = json.loads((root / "recovery-interruption.json").read_text(encoding="utf-8"))
    restart = json.loads((root / "recovery-restart.json").read_text(encoding="utf-8"))
    run = next(run for run in runs if run["task_id"] == before["task_id"])
    assert before["signal"] == "SIGKILL"
    assert restart["old_pid_exists"] is False
    assert len(restart["workers"]) == 1
    assert restart["workers"][0]["pid"] != before["worker_pid"]
    assert run["state"] == "succeeded"
    original = before["provider_jobs_before"][0]
    assert original["state"] in {"accepted", "running"}
    assert before["operation_before"]["provider_request_id"] == original["request_id"]
    matching = [job for job in run["ocr_jobs"] if job["operation_id"] == original["operation_id"]]
    assert len(matching) == 1
    assert matching[0]["request_id"] == original["request_id"]
    assert matching[0]["state"] == "succeeded"
    operations = [op for op in run["operations"] if op["operation_id"] == original["operation_id"]]
    assert len(operations) == 1 and operations[0]["state"] == "succeeded"
    assert operations[0]["provider_request_id"] == original["request_id"]
