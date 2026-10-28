"""Check whether the monitor has completed a recent poll."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv

from .config import poll_interval, state_db_path


def is_healthy(filename: str, interval: int, now: float | None = None) -> bool:
    try:
        uri = Path(filename).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            row = conn.execute("SELECT updated_at FROM monitor_heartbeat WHERE id=1").fetchone()
        current = time.time() if now is None else now
        return row is not None and current - row[0] <= max(3600, interval * 3)
    except (OSError, sqlite3.Error, TypeError):
        return False


def main() -> None:
    load_dotenv()
    raise SystemExit(0 if is_healthy(state_db_path(), poll_interval()) else 1)


if __name__ == "__main__":
    main()
