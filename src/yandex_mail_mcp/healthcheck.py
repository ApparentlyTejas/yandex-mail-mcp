"""Check whether the monitor has completed a recent poll."""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .config import env_int


def is_healthy(filename: str, interval: int, now: float | None = None) -> bool:
    try:
        uri = Path(filename).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            row = conn.execute("SELECT updated_at FROM monitor_heartbeat WHERE id=1").fetchone()
        current = time.time() if now is None else now
        return row is not None and 0 <= current - row[0] <= max(3600, interval * 3)
    except (OSError, sqlite3.Error, TypeError):
        return False


def main() -> None:
    filename = os.getenv("STATE_DB", "./data/mail_state.sqlite3")
    interval = max(60, env_int("POLL_INTERVAL_SECONDS", 180))
    raise SystemExit(0 if is_healthy(filename, interval) else 1)


if __name__ == "__main__":
    main()
