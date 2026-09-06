"""T020 v5 migration and transaction regression coverage."""

import sqlite3
import threading

import pytest

from letsaigc.pipelines import migrations
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.ledger import Ledger
from letsaigc.pipelines.service import PipelineService


def test_v5_migration_adds_only_parent_child_tables_and_keeps_v4_rows(tmp_path):
    service = PipelineService(tmp_path, ui_schema=4)
    plan = service.smoke_plan("v4-history")
    with service.ledger.transaction() as db:
        before = [tuple(row) for row in db.execute("SELECT * FROM tasks")]
    backup = migrations.migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
    with service.ledger.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {
            "ui_budget_groups",
            "ui_child_bindings",
            "ui_operation_charges",
        }
        assert [tuple(row) for row in db.execute("SELECT * FROM tasks")] == before
    assert service.ledger.plan(plan.task_id) == plan


def test_v5_migration_requires_stopped_writers_and_rolls_back(tmp_path, monkeypatch):
    service = PipelineService(tmp_path, ui_schema=4)
    with pytest.raises(PipelineError):
        migrations.migrate_ui_ledger(service.ledger.path, writers_stopped=False, target_version=5)

    def fail(db):
        db.execute("CREATE TABLE t020_partial (id INTEGER)")
        raise RuntimeError("injected")

    monkeypatch.setattr(migrations, "_upgrade_v5", fail)
    with pytest.raises(RuntimeError, match="injected"):
        migrations.migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    with service.ledger.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='t020_partial'").fetchone()


def test_v5_migration_materializes_existing_ui_root_budget(tmp_path):
    from letsaigc.schemas.ui import UIAnalysisRequest

    service = PipelineService(tmp_path, ui_schema=4)
    image = service.artifacts.put("ui-root", "input", b"image", role="original")
    plan = service.ui_plan(
        "ui-root",
        UIAnalysisRequest(
            input={"kind": "manual", "inputs": [image]},
            budget={
                "max_total_cost_usd": 1,
                "max_iteration_cost_usd": 1,
                "max_total_gpu_minutes": 0,
                "max_iteration_gpu_minutes": 0,
                "max_revisions": 0,
            },
        ),
    )
    migrations.migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=5)
    with service.ledger.transaction() as db:
        row = db.execute(
            "SELECT budget FROM ui_budget_groups WHERE root_task_id=?", (plan.task_id,)
        ).fetchone()
    assert row is not None and '"max_total_cost_usd":1.0' in row["budget"]


def test_v4_single_task_cancel_and_gate_still_work_without_v5_tables(tmp_path):
    service = PipelineService(tmp_path, ui_schema=4)
    plan = service.smoke_plan("legacy")
    service.ledger.request_cancel(plan.task_id)
    with service.ledger.transaction() as db:
        assert db.execute("SELECT cancelled FROM tasks WHERE task_id=?", (plan.task_id,)).fetchone()[0] == 1
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4


def test_concurrent_legacy_ledgers_serialize_writes(tmp_path):
    # This is intentionally small: BEGIN IMMEDIATE must serialize a pair of
    # independent connections, while the second writer remains valid after it
    # waits for the first one to commit.
    ledger = Ledger(tmp_path / "ledger.sqlite", initialize_ui=5)
    entered = threading.Event()
    release = threading.Event()
    errors = []

    def first():
        try:
            with ledger.transaction() as db:
                db.execute("CREATE TABLE IF NOT EXISTS t020_lock (id INTEGER)")
                entered.set()
                release.wait(5)
        except BaseException as exc:  # pragma: no cover - diagnostic only
            errors.append(exc)

    def second():
        try:
            entered.wait(5)
            with ledger.transaction() as db:
                db.execute("INSERT INTO t020_lock VALUES (1)")
        except BaseException as exc:  # pragma: no cover - diagnostic only
            errors.append(exc)

    left = threading.Thread(target=first)
    right = threading.Thread(target=second)
    left.start()
    assert entered.wait(5)
    right.start()
    release.set()
    left.join(5)
    right.join(5)
    assert not errors
    with ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM t020_lock").fetchone()[0] == 1
