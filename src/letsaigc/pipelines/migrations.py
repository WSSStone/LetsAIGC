"""Explicit offline migrations; opening an old ledger never silently upgrades it."""

import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from .errors import PipelineError


def _upgrade_v2(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE ui_step_bindings (
        task_id TEXT NOT NULL REFERENCES tasks(task_id), step_id TEXT NOT NULL,
        revision INTEGER NOT NULL, input_hash TEXT NOT NULL, binding TEXT NOT NULL,
        outputs TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY(task_id,step_id,revision))""")
    db.execute("PRAGMA user_version=2")


def _upgrade_v3(db):
    db.execute("""CREATE TABLE quota_scopes (
        scope_id TEXT PRIMARY KEY, provider TEXT NOT NULL, account_alias TEXT NOT NULL,
        credential_hash TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 1,
        UNIQUE(provider,account_alias))""")
    db.execute("""CREATE TABLE quota_snapshots (
        snapshot_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL REFERENCES quota_scopes(scope_id),
        generation INTEGER NOT NULL, observed_at INTEGER NOT NULL, payload TEXT NOT NULL)""")
    db.execute("""CREATE TABLE quota_routes (
        operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id) DEFERRABLE INITIALLY DEFERRED,
        root_task_id TEXT NOT NULL REFERENCES tasks(task_id), logical_query_id TEXT NOT NULL,
        attempt_no INTEGER NOT NULL, provider TEXT NOT NULL, payload TEXT NOT NULL,
        UNIQUE(root_task_id,logical_query_id,attempt_no))""")
    db.execute("""CREATE TABLE quota_reservations (
        operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id) DEFERRABLE INITIALLY DEFERRED,
        scope_id TEXT NOT NULL REFERENCES quota_scopes(scope_id), snapshot_id TEXT NOT NULL,
        unit TEXT NOT NULL, units INTEGER NOT NULL, state TEXT NOT NULL,
        covered_watermark TEXT)""")
    db.execute("""CREATE TABLE quota_probes (
        operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id) DEFERRABLE INITIALLY DEFERRED,
        root_task_id TEXT NOT NULL REFERENCES tasks(task_id), scope_id TEXT NOT NULL,
        provider TEXT NOT NULL, started_at INTEGER NOT NULL, state TEXT NOT NULL)""")
    db.execute("PRAGMA user_version=3")


def _upgrade_v4(db):
    db.execute("""CREATE TABLE ui_review_heads (
        task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
        draft_revision INTEGER NOT NULL DEFAULT 0, confirmed_revision INTEGER,
        base_refs TEXT NOT NULL)""")
    db.execute("""CREATE TABLE ui_review_revisions (
        task_id TEXT NOT NULL REFERENCES ui_review_heads(task_id), revision INTEGER NOT NULL,
        draft_ref TEXT NOT NULL, patch_ref TEXT, published_refs TEXT,
        PRIMARY KEY(task_id,revision))""")
    db.execute("""CREATE TABLE ui_review_requests (
        task_id TEXT NOT NULL REFERENCES ui_review_heads(task_id), request_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
        result TEXT NOT NULL, PRIMARY KEY(task_id,request_id))""")
    db.execute("PRAGMA user_version=4")


def _upgrade_v5(db):
    """Add the parent/child UI bookkeeping without copying operation money.

    ``operations`` remains the only source of monetary and GPU values.  The
    three tables below carry ownership, immutable request/selection metadata,
    and a unique operation grouping row used for counts and projections.
    """
    db.execute("""CREATE TABLE ui_budget_groups (
        root_task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
        budget TEXT NOT NULL,
        resource_limits TEXT NOT NULL DEFAULT '{}',
        source_limits TEXT NOT NULL DEFAULT '{}',
        submission_gate TEXT NOT NULL DEFAULT 'open',
        stop_reason TEXT,
        revision_count INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE ui_child_bindings (
        task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
        parent_task_id TEXT NOT NULL REFERENCES tasks(task_id),
        root_task_id TEXT NOT NULL REFERENCES ui_budget_groups(root_task_id),
        purpose TEXT NOT NULL,
        source_ids TEXT NOT NULL,
        request_ref TEXT NOT NULL,
        selection_ref TEXT NOT NULL,
        selection_revision INTEGER NOT NULL,
        selection_hash TEXT NOT NULL,
        budget TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        active INTEGER NOT NULL DEFAULT 0,
        fingerprint TEXT NOT NULL,
        source_chain TEXT NOT NULL DEFAULT '[]',
        edit_chain TEXT NOT NULL DEFAULT '[]'
    )""")
    db.execute("""CREATE INDEX ui_child_bindings_root_idx
        ON ui_child_bindings(root_task_id, purpose, status)""")
    db.execute("""CREATE TABLE ui_operation_charges (
        operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
        root_task_id TEXT NOT NULL REFERENCES ui_budget_groups(root_task_id),
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        capability TEXT NOT NULL,
        source_ids TEXT NOT NULL DEFAULT '[]',
        source_chain TEXT NOT NULL DEFAULT '[]',
        edit_chain TEXT NOT NULL DEFAULT '[]',
        revision INTEGER NOT NULL,
        revision_units INTEGER NOT NULL DEFAULT 0 CHECK(revision_units IN (0,1)),
        charge_state TEXT NOT NULL DEFAULT 'reserved'
    )""")
    # A v4 ledger may already contain planned UI roots.  Materialize their
    # immutable budgets while the migration transaction still owns the write
    # lock, so the first child cannot race a missing group.
    for row in db.execute("SELECT task_id,plan FROM tasks").fetchall():
        plan = json.loads(row["plan"] if isinstance(row, sqlite3.Row) else row[1])
        if plan.get("workflow_type") == "ui_analysis":
            db.execute(
                "INSERT OR IGNORE INTO ui_budget_groups(root_task_id,budget) VALUES(?,?)",
                (row["task_id"] if isinstance(row, sqlite3.Row) else row[0], json.dumps(
                    plan["envelope"]["budget"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )),
            )
    db.execute("PRAGMA user_version=5")


def migrate_ui_ledger(path: Path, *, writers_stopped: bool, target_version: int = 2) -> Path | None:
    if not writers_stopped:
        raise PipelineError("migration_requires_stop", "Stop ledger writers before upgrading")
    path = path.resolve(strict=True)
    db = sqlite3.connect(path, timeout=1, isolation_level=None)
    try:
        db.execute("BEGIN EXCLUSIVE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if target_version not in {2, 3, 4, 5}:
            raise PipelineError("ledger_version")
        if version in {2, 3, 4, 5} and version >= target_version:
            db.rollback()
            return None
        if version not in {1, 2, 3, 4}:
            raise PipelineError("ledger_version", "Unsupported UI ledger migration source")
        backup = path.with_name(path.name + f".v{version}-backup-" + uuid4().hex)
        with backup.open("xb") as output:
            output.write(db.serialize())
            output.flush()
            os.fsync(output.fileno())
        if version == 1:
            _upgrade_v2(db)
        if target_version >= 3 and version < 3:
            _upgrade_v3(db)
        if target_version >= 4 and version < 4:
            _upgrade_v4(db)
        if target_version >= 5 and version < 5:
            _upgrade_v5(db)
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise PipelineError("migration_integrity")
        db.commit()
        return backup
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def main():
    import argparse

    from ..execution.temporal.config import runtime_root

    parser = argparse.ArgumentParser(description="Offline UI ledger upgrade; stop all workers first")
    parser.add_argument("--ledger", type=Path, default=runtime_root() / "ledger.sqlite")
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--target-version", type=int, choices=[2, 3, 4, 5], default=5)
    args = parser.parse_args()
    try:
        backup = migrate_ui_ledger(
            args.ledger, writers_stopped=args.writers_stopped, target_version=args.target_version
        )
    except (PipelineError, OSError):
        parser.error("Migration failed; confirm the ledger exists and all writers are stopped")
    print("migrated_with_backup" if backup else "already_current")


if __name__ == "__main__":
    main()
