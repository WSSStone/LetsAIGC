"""Bounded atomic publication for local files on Windows."""

import os
import time
from pathlib import Path


def replace_file(source: Path, destination: Path) -> None:
    """Retry only transient Windows access/sharing errors, without weakening ACLs."""
    delays = (0.05, 0.1, 0.2, 0.4)
    for attempt in range(len(delays) + 1):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == len(delays):
                raise
            time.sleep(delays[attempt])
