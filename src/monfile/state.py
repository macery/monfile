"""SQLite state store and process lock (DESIGN §8)."""

from __future__ import annotations

from pathlib import Path

SCHEMA_VERSION = 1
LOCK_PATH = Path("/run/monfile/monfile.lock")


class StateStore:
    """files / action_results / meta tables (WAL mode, mode 0600)."""

    def __init__(self, db_path: Path) -> None:
        raise NotImplementedError

    def recover(self) -> None:
        """At startup, move IN_PROGRESS records back to READY."""
        raise NotImplementedError


class ProcessLock:
    """Non-blocking fcntl.flock on LOCK_PATH. Raises if another instance holds it."""

    def __init__(self, path: Path = LOCK_PATH) -> None:
        raise NotImplementedError
