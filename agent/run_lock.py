"""Allow only one cancellation agent at a time, whether it was started from a notification or the dashboard.

The lock is a small JSON file next to the database. A lock older than STALE_AFTER is treated as left behind by a
crashed run and ignored, so a crash can never block cancellations for good.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

STALE_AFTER = timedelta(minutes=15)


class AgentBusy(RuntimeError):
    def __init__(self, holder: dict):
        super().__init__(f"A cancellation for {holder.get('merchant', 'another subscription')} is already running")
        self.holder = holder


def lock_path(database_path: str | Path) -> Path:
    return Path(database_path).with_suffix(".agent.lock")


def current(database_path: str | Path) -> dict | None:
    """The running cancellation ({merchant, started}), or None when nothing is running."""
    path = lock_path(database_path)
    try:
        holder = json.loads(path.read_text(encoding="utf-8"))
        started = datetime.fromisoformat(holder["started"])
    except (OSError, ValueError, KeyError):
        return None
    return holder if datetime.now(timezone.utc) - started < STALE_AFTER else None


@contextmanager
def agent_lock(database_path: str | Path, merchant: str):
    path = lock_path(database_path)
    holder = current(database_path)
    if holder:
        raise AgentBusy(holder)
    path.unlink(missing_ok=True)  # only a stale or unreadable lock can be here
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:  # another run took the lock between the check and here
        raise AgentBusy(current(database_path) or {}) from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"merchant": merchant, "started": datetime.now(timezone.utc).isoformat()}, handle)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
