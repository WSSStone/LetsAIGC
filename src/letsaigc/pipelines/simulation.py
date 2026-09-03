"""Local deterministic provider with a durable receipt independent of the ledger."""

import sqlite3
from pathlib import Path

from ..schemas.pipeline import Cost, canonical_json
from .contracts import Capability, Observation, Submission


class SimulationBackend:
    capability = Capability(id="simulation.generate", idempotent_submission=True, can_cancel=True)

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS receipts(
                request_id TEXT PRIMARY KEY, content TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0)""")

    def submit(self, operation_id: str, arguments: dict) -> Submission:
        content = canonical_json({"operation_id": operation_id, "simulated": True, "revision": arguments["revision"]})
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("INSERT OR IGNORE INTO receipts(request_id,content) VALUES(?,?)", (operation_id, content))
        return Submission(request_id=operation_id)

    def recover(self, operation_id: str) -> Submission | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT 1 FROM receipts WHERE request_id=?", (operation_id,)).fetchone()
        return Submission(request_id=operation_id) if row else None

    def inspect(self, submission: Submission) -> Observation:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT cancelled FROM receipts WHERE request_id=?", (submission.request_id,)).fetchone()
        return Observation(state="unknown" if row is None else "failed" if row[0] else "succeeded", actual=Cost())

    def collect(self, submission: Submission) -> list[tuple[str, bytes, str]]:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT content FROM receipts WHERE request_id=?", (submission.request_id,)).fetchone()
        if row is None:
            raise ValueError("Simulation receipt is missing")
        return [("simulation_result", row[0].encode(), "application/json")]

    def cancel(self, submission: Submission) -> Observation:
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE receipts SET cancelled=1 WHERE request_id=?", (submission.request_id,))
        return self.inspect(submission)
