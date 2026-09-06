import sqlite3

import pytest

from letsaigc.pipelines import migrations
from letsaigc.pipelines.errors import PipelineError
from letsaigc.pipelines.ledger import Ledger
from letsaigc.pipelines.service import PipelineService


def test_v4_adds_only_review_tables_and_preserves_v3_unknown_evidence(tmp_path):
    service = PipelineService(tmp_path, ui_schema=3)
    plan = service.smoke_plan("historic")
    with service.ledger.transaction() as db:
        db.execute(
            "INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("unknown-op", plan.task_id, "legacy", 0, plan.fingerprint, "outcome_unknown", None, 250000, 0, 0, 0, "{}"),
        )
        before = {
            name: [tuple(r) for r in db.execute(f"SELECT * FROM {name}")]
            for name in ("tasks", "approvals", "operations", "ui_step_bindings", "quota_scopes")
        }
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    backup = migrations.migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=4)
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    reopened = Ledger(service.ledger.path)
    with reopened.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} - tables == {
            "ui_review_heads",
            "ui_review_revisions",
            "ui_review_requests",
        }
        for name, rows in before.items():
            assert [tuple(r) for r in db.execute(f"SELECT * FROM {name}")] == rows
    assert reopened.plan(plan.task_id).fingerprint == plan.fingerprint
    assert reopened.usage(plan.task_id)["unsettled"].cost_usd == 0.25
    assert migrations.migrate_ui_ledger(service.ledger.path, writers_stopped=True, target_version=4) is None


def test_v4_requires_stop_and_rolls_back_injected_failure(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "ledger.sqlite", initialize_ui=3)
    with pytest.raises(PipelineError):
        migrations.migrate_ui_ledger(ledger.path, writers_stopped=False, target_version=4)

    def fail(db):
        db.execute("CREATE TABLE partial_review (id INTEGER)")
        raise RuntimeError("injected failure")

    monkeypatch.setattr(migrations, "_upgrade_v4", fail)
    with pytest.raises(RuntimeError):
        migrations.migrate_ui_ledger(ledger.path, writers_stopped=True, target_version=4)
    with ledger.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='partial_review'").fetchone()
    assert list(tmp_path.glob("*.v3-backup-*"))
