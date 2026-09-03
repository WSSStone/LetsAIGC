from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from letsaigc.assets.store import ArtifactStore
from letsaigc.pipelines.approval import approve
from letsaigc.pipelines.contracts import Capability, Observation, Submission
from letsaigc.pipelines.errors import OutcomeUnknown, PipelineError
from letsaigc.pipelines.ledger import Ledger
from letsaigc.pipelines.resources import owners
from letsaigc.pipelines.service import PipelineService
from letsaigc.schemas import TaskBudget
from letsaigc.schemas.pipeline import (
    ApprovalEnvelope,
    ApprovalRequest,
    Cost,
    PipelinePlan,
    operation_id,
    validate_payload,
)


def paid_plan(task_id="paid", *, revisions=2):
    return PipelinePlan(
        task_id=task_id,
        workflow_type="temporal_smoke",
        envelope=ApprovalEnvelope(
            allowed_capabilities=["simulation.generate"],
            budget=TaskBudget(
                max_total_cost_usd=2,
                max_iteration_cost_usd=1,
                max_total_gpu_minutes=2,
                max_iteration_gpu_minutes=1,
                max_revisions=revisions,
            ),
        ),
        estimated_iteration=Cost(cost_usd=1, gpu_minutes=1),
    )


def authorized(tmp_path, plan=None):
    service = PipelineService(tmp_path)
    plan = plan or paid_plan()
    service.ledger.register(plan)
    receipt = approve(service.ledger, plan.task_id, plan.fingerprint)
    assert service.ledger.consume_approval(receipt)
    return service, plan


def reserve_process(path: str, serialized: str, revision: int, resource: str | None = None):
    ledger = Ledger(Path(path))
    plan = PipelinePlan.model_validate_json(serialized)
    try:
        return ledger.reserve(plan, "generate", revision, plan.estimated_iteration, resource=resource).operation_id
    except PipelineError as exc:
        return exc.code


def test_atomic_budget_and_process_competition(tmp_path):
    service, plan = authorized(tmp_path)
    with ProcessPoolExecutor(3) as pool:
        results = list(
            pool.map(reserve_process, [str(service.ledger.path)] * 3, [plan.model_dump_json()] * 3, range(3))
        )
    assert results.count("total_budget") == 1
    assert service.ledger.usage(plan.task_id)["reserved"] == Cost(cost_usd=2, gpu_minutes=2)


def test_idempotent_settlement_and_unknown_reservation(tmp_path):
    service, plan = authorized(tmp_path)
    ledger = service.ledger
    first = ledger.reserve(plan, "generate", 0, plan.estimated_iteration, resource="local-gpu")
    assert ledger.reserve(plan, "generate", 0, plan.estimated_iteration) == first
    ledger.begin_submit(first.operation_id)
    ledger.uncertain(first.operation_id)
    assert ledger.usage(plan.task_id) == {
        "actual": Cost(),
        "reserved": Cost(),
        "unsettled": plan.estimated_iteration,
    }
    assert owners(ledger)[0]["uncertain"] == 1

    with pytest.raises(PipelineError, match="owned"):
        ledger.reserve(plan, "generate", 1, plan.estimated_iteration, resource="local-gpu")
    ledger.submitted(first.operation_id, "receipt-1", {})
    ledger.finish(first.operation_id, plan.estimated_iteration, {"done": True})
    ledger.finish(first.operation_id, plan.estimated_iteration, {"done": True})
    assert owners(ledger) == []
    with pytest.raises(PipelineError, match="settlement"):
        ledger.finish(first.operation_id, Cost(), {"done": True})
    assert ledger.usage(plan.task_id)["actual"] == plan.estimated_iteration


@pytest.mark.parametrize("persistent", [False, True])
def test_atomic_publication_tolerates_only_bounded_windows_sharing_errors(tmp_path, monkeypatch, persistent):
    import os

    import letsaigc.files as module

    if os.name != "nt":
        pytest.skip("Windows sharing violation regression")
    source, target = tmp_path / "new.tmp", tmp_path / "target.json"
    source.write_text("new")
    target.write_text("old")
    original = os.replace
    attempts = []

    def sharing(src, dest):
        attempts.append(1)
        if persistent or len(attempts) == 1:
            error = PermissionError("sharing violation")
            error.winerror = 32
            raise error
        return original(src, dest)

    monkeypatch.setattr(module.os, "replace", sharing)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    if persistent:
        with pytest.raises(PermissionError):
            module.replace_file(source, target)
        assert len(attempts) == 5
        assert target.read_text() == "old"
    else:
        module.replace_file(source, target)
        assert len(attempts) == 2
        assert target.read_text() == "new"


def test_approval_is_exact_local_and_cancel_blocks_submit(tmp_path):
    service = PipelineService(tmp_path)
    plan = paid_plan()
    service.ledger.register(plan)
    fake = ApprovalRequest(
        request_id="forged", task_id=plan.task_id, plan_fingerprint=plan.fingerprint, decision="approve"
    )
    with pytest.raises(PipelineError, match="receipt"):
        service.ledger.consume_approval(fake)
    with pytest.raises(PipelineError):
        approve(service.ledger, plan.task_id, "0" * 64)
    receipt = approve(service.ledger, plan.task_id, plan.fingerprint)
    assert receipt == approve(service.ledger, plan.task_id, plan.fingerprint)
    service.ledger.consume_approval(receipt)
    operation = service.ledger.reserve(plan, "generate", 0, plan.estimated_iteration)
    service.ledger.request_cancel(plan.task_id)
    with pytest.raises(PipelineError) as caught:
        service.ledger.begin_submit(operation.operation_id)
    assert caught.value.code == "cancelled"
    assert service.cancel(plan, 0)
    assert service.ledger.usage(plan.task_id)["reserved"] == Cost()


def test_plan_identity_and_registry_are_immutable(tmp_path):
    service, plan = authorized(tmp_path)
    changed = plan.model_copy(update={"parameters": {"accept_after_revision": 1}})
    with pytest.raises(PipelineError):
        service.ledger.register(changed)
    with pytest.raises(PipelineError):
        service.ledger.register(plan.model_copy(update={"workflow_type": "shell"}))
    assert operation_id(plan, "generate", 0) != operation_id(plan, "generate", 1)
    assert operation_id(plan, "generate", 0) == operation_id(
        PipelinePlan.model_validate_json(plan.model_dump_json()), "generate", 0
    )


@pytest.mark.parametrize(
    "value",
    [
        {"token": "fake-sentinel"},
        {"url": "https://example.test/a?key=fake-sentinel"},
        {"url": "https://example.test/a#fake-sentinel"},
        {"path": "C:\\private\\file"},
        {"path": "/tmp/private"},
        "a" * (64 * 1024 + 1),
    ],
    ids=["credential", "query", "fragment", "windows-path", "posix-path", "oversized"],
)
def test_payload_policy(value):
    with pytest.raises(ValueError):
        validate_payload(value)


def test_artifact_scope_integrity_and_atomic_repeat(tmp_path):
    store = ArtifactStore(tmp_path)
    ref = store.put("task", "op", b"immutable", role="r" * 128)
    assert store.put("task", "op", b"immutable", role="r" * 128) == ref
    assert store.read(ref) == b"immutable"
    with pytest.raises(ValidationError):
        ref.model_validate({**ref.model_dump(), "key": "task/../outside"})
    with pytest.raises(ValidationError):
        ref.model_validate({**ref.model_dump(), "key": "another/a"})
    store.resolve(ref).write_bytes(b"changed")
    with pytest.raises(PipelineError) as caught:
        store.read(ref)
    assert caught.value.code == "artifact_changed"
    store.resolve(ref).unlink()
    with pytest.raises(PipelineError) as caught:
        store.read(ref)
    assert caught.value.code == "missing_artifact"


class DisconnectingBackend:
    capability = Capability(id="simulation.generate", resource="local-gpu", can_cancel=True)

    def __init__(self, recoverable):
        self.calls = 0
        self.recoverable = recoverable

    def submit(self, key, arguments):
        self.calls += 1
        raise OSError("https://provider.test?key=fake-sentinel")

    def recover(self, key):
        return Submission(request_id=key) if self.recoverable else None

    def inspect(self, receipt):
        return Observation(state="succeeded", actual=Cost(cost_usd=1, gpu_minutes=1))

    def collect(self, receipt):
        return [("result", b"done", "text/plain")]

    def cancel(self, receipt):
        return Observation(state="unknown")


@pytest.mark.parametrize("recoverable", [True, False])
def test_accepted_then_disconnected_never_resubmits(tmp_path, recoverable):
    service, plan = authorized(tmp_path)
    backend = DisconnectingBackend(recoverable)
    service.backends["simulation.generate"] = backend
    with pytest.raises(OutcomeUnknown):
        service.submit(plan, 0)
    key = operation_id(plan, "generate", 0)
    if recoverable:
        service.submit(plan, 0)
        service.observe(plan, key)
        result = service.collect(plan, key)
        assert result == service.collect(plan, key)
        assert result.actual.cost_usd == 1
        assert len(result.result["artifacts"]) == 1
    else:
        with pytest.raises(OutcomeUnknown):
            service.submit(plan, 0)
        assert not service.cancel(plan, 0)
        assert owners(service.ledger)
        assert service.ledger.usage(plan.task_id)["unsettled"].cost_usd == 1
    assert backend.calls == 1


def test_unsupported_ledger_migration_is_rejected(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    with ledger.transaction() as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(PipelineError) as caught:
        Ledger(ledger.path)
    assert caught.value.code == "ledger_version"


def test_two_tasks_compete_for_single_gpu_across_processes(tmp_path):
    service, first = authorized(tmp_path, paid_plan("first"))
    _, second = authorized(tmp_path, paid_plan("second"))
    with ProcessPoolExecutor(2) as pool:
        results = list(
            pool.map(
                reserve_process,
                [str(service.ledger.path)] * 2,
                [first.model_dump_json(), second.model_dump_json()],
                [0, 0],
                ["local-gpu"] * 2,
            )
        )
    assert results.count("resource_busy") == 1
    assert len(owners(service.ledger)) == 1
    owner = owners(service.ledger)[0]["operation_id"]
    service.ledger.uncertain(owner)
    assert owners(service.ledger)[0]["operation_id"] == owner


def test_actual_overrun_is_recorded_and_blocks_further_budget(tmp_path):
    service, plan = authorized(tmp_path)
    record = service.ledger.reserve(plan, "generate", 0, plan.estimated_iteration)
    settled = service.ledger.finish(record.operation_id, Cost(cost_usd=2.1), {"provider_done": True})
    assert settled.result["budget_exceeded"]
    assert service.ledger.usage(plan.task_id)["actual"].cost_usd == 2.1
    with pytest.raises(PipelineError) as caught:
        service.ledger.reserve(plan, "generate", 1, plan.estimated_iteration)
    assert caught.value.code == "total_budget"


def test_interrupted_preparation_cannot_submit_late_and_lost_receipt_is_unsettled(tmp_path):
    service, plan = authorized(tmp_path)
    ledger = service.ledger
    prepared = ledger.reserve(plan, "generate", 0, plan.estimated_iteration, resource="local-gpu")
    ledger.record_interruption(prepared.operation_id)
    assert not ledger.begin_submit(prepared.operation_id)
    assert ledger.get(prepared.operation_id).state == "failed"
    assert not owners(ledger)
    submitted = ledger.reserve(plan, "generate", 1, plan.estimated_iteration, resource="local-gpu")
    ledger.begin_submit(submitted.operation_id)
    ledger.record_interruption(submitted.operation_id)
    assert ledger.get(submitted.operation_id).state == "outcome_unknown"
    assert ledger.usage(plan.task_id)["unsettled"] == plan.estimated_iteration
    assert ledger.usage(plan.task_id)["reserved"] == Cost()
    assert owners(ledger)[0]["uncertain"] == 1
