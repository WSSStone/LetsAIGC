"""Explicit offline migrations; opening an old ledger never silently upgrades it."""

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


def migrate_ui_ledger(path: Path, *, writers_stopped: bool, target_version: int = 2) -> Path | None:
    if not writers_stopped:
        raise PipelineError("migration_requires_stop", "Stop ledger writers before upgrading")
    path = path.resolve(strict=True)
    db = sqlite3.connect(path, timeout=1, isolation_level=None)
    try:
        db.execute("BEGIN EXCLUSIVE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if target_version not in {2, 3}:
            raise PipelineError("ledger_version")
        if version in {2, 3} and version >= target_version:
            db.rollback()
            return None
        if version not in {1, 2}:
            raise PipelineError("ledger_version", "Unsupported UI ledger migration source")
        backup = path.with_name(path.name + f".v{version}-backup-" + uuid4().hex)
        with backup.open("xb") as output:
            output.write(db.serialize())
            output.flush()
            os.fsync(output.fileno())
        if version == 1:
            _upgrade_v2(db)
        if target_version == 3:
            _upgrade_v3(db)
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
    parser.add_argument("--target-version", type=int, choices=[2, 3], default=3)
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
