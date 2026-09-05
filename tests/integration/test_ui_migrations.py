import sqlite3

import pytest

from letsaigc.pipelines import migrations
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.ledger import Ledger
from letsaigc.pipelines.service import PipelineService


def test_v2_adds_only_bindings_preserving_old_plan_and_backup(tmp_path):
    service = PipelineService(tmp_path)
    plan = service.smoke_plan("old-task")
    with service.ledger.transaction() as db:
        before = tuple(db.execute("SELECT * FROM tasks").fetchone())
    backup = migrations.migrate_ui_ledger(service.ledger.path, writers_stopped=True)
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT * FROM tasks").fetchone() == before
    reopened = Ledger(service.ledger.path)
    assert reopened.plan(plan.task_id) == plan
    with reopened.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert tuple(db.execute("SELECT * FROM tasks").fetchone()) == before
        assert {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "tasks",
            "approvals",
            "operations",
            "resources",
            "projections",
            "ui_step_bindings",
        }
    assert migrations.migrate_ui_ledger(service.ledger.path, writers_stopped=True) is None


def test_migration_refuses_running_writers_and_rolls_back_failure(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    with pytest.raises(PipelineError):
        migrations.migrate_ui_ledger(ledger.path, writers_stopped=False)

    def fail(db):
        db.execute("CREATE TABLE temporary_partial_upgrade (id INTEGER)")
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(migrations, "_upgrade_v2", fail)
    with pytest.raises(RuntimeError, match="injected"):
        migrations.migrate_ui_ledger(ledger.path, writers_stopped=True)
    with ledger.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='temporary_partial_upgrade'").fetchone()
    assert list(tmp_path.glob("*.v1-backup-*"))


def test_ui_initialization_never_upgrades_existing_legacy_ledger(tmp_path):
    old = PipelineService(tmp_path / "old")
    PipelineService(tmp_path / "old", ui_schema=True)
    with old.ledger.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    fresh = PipelineService(tmp_path / "fresh", ui_schema=True)
    with fresh.ledger.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_v3_adds_five_quota_tables_and_preserves_v2_on_failure(tmp_path, monkeypatch):
    service = PipelineService(tmp_path, ui_schema=True)
    plan = service.smoke_plan("preserved")
    upgrade = migrations._upgrade_v3

    def fail(db):
        upgrade(db)
        raise RuntimeError("injected v3 failure")

    with monkeypatch.context() as patch:
        patch.setattr(migrations, "_upgrade_v3", fail)
        with pytest.raises(RuntimeError, match="injected"):
            migrations.migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=3)
    with service.ledger.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'quota_%'").fetchall()
    backup = migrations.migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=3)
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert service.ledger.plan(plan.task_id) == plan
    with service.ledger.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'quota_%'")
        } == {"quota_scopes", "quota_snapshots", "quota_routes", "quota_reservations", "quota_probes"}
