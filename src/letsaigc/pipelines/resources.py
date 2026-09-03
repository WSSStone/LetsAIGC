"""GPU ownership is persisted with operations, never reclaimed on timer expiry."""

from .ledger import Ledger


def owners(ledger: Ledger) -> list[dict]:
    with ledger.transaction() as db:
        return [dict(row) for row in db.execute("SELECT * FROM resources ORDER BY resource")]
