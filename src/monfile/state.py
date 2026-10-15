"""SQLite state store and process lock (DESIGN §8)."""

from __future__ import annotations

import fcntl
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from pathlib import Path

SCHEMA_VERSION = 1
LOCK_PATH = Path("/run/monfile/monfile.lock")

_FILE_COLUMNS = """
    folder_id TEXT NOT NULL, rel_path BLOB NOT NULL,
    dev INTEGER, ino INTEGER, size INTEGER, mtime_ns INTEGER,
    first_seen REAL, stable_since REAL, hash_start TEXT, hash_end TEXT,
    marker_seen_at REAL, status TEXT NOT NULL, gone INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, updated_at REAL
"""

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS files ({_FILE_COLUMNS}, PRIMARY KEY (folder_id, rel_path));
CREATE TABLE IF NOT EXISTS files_history ({_FILE_COLUMNS}, archived_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS action_results (
    folder_id TEXT NOT NULL, rel_path BLOB NOT NULL, source_key TEXT NOT NULL,
    action_index INTEGER NOT NULL, action_type TEXT NOT NULL, ok INTEGER NOT NULL,
    destination BLOB, dest_sha256 TEXT, bytes INTEGER, started REAL, finished REAL, error TEXT,
    PRIMARY KEY (folder_id, rel_path, source_key, action_index));
CREATE TABLE IF NOT EXISTS notices (
    folder_id TEXT NOT NULL, rel_path BLOB NOT NULL, kind TEXT NOT NULL,
    PRIMARY KEY (folder_id, rel_path, kind));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _enc(rel_path: str) -> bytes:
    """Paths are stored as raw bytes so non-UTF-8 filenames round-trip."""
    return os.fsencode(rel_path)


def _dec(raw: bytes) -> str:
    return os.fsdecode(raw)


@dataclass(frozen=True)
class FileRecord:
    folder_id: str
    rel_path: str
    dev: int
    ino: int
    size: int
    mtime_ns: int
    first_seen: float
    stable_since: float
    hash_start: str | None
    hash_end: str | None
    marker_seen_at: float | None
    status: str
    gone: int = 0
    attempts: int = 0
    last_error: str | None = None
    updated_at: float = 0.0

    @property
    def stat_key(self) -> tuple[int, int, int, int]:
        return (self.dev, self.ino, self.size, self.mtime_ns)

    def with_(self, **changes: object) -> FileRecord:
        return replace(self, **changes)  # type: ignore[arg-type]


_FIELD_NAMES = [f.name for f in fields(FileRecord)]


@dataclass(frozen=True)
class ActionRecord:
    action_index: int
    action_type: str
    ok: bool
    destination: str | None
    dest_sha256: str | None
    bytes: int
    started: float
    finished: float
    error: str | None


class StateStore:
    """files / files_history / action_results / notices / meta tables (WAL mode, mode 0600)."""

    def __init__(self, db_path: Path, *, readonly: bool = False) -> None:
        self.path = db_path
        if readonly:
            self.db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, isolation_level=None, timeout=30)
            return
        fd = os.open(db_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(db_path, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        with self.tx():
            for stmt in _SCHEMA.strip().split(";"):
                if stmt.strip():
                    self.db.execute(stmt)
            row = self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                self.db.execute("INSERT INTO meta VALUES ('schema_version', ?), ('created_at', ?)",
                                (str(SCHEMA_VERSION), str(time.time())))
            elif int(row[0]) != SCHEMA_VERSION:
                raise RuntimeError(f"{db_path}: schema version {row[0]} is not supported (expected {SCHEMA_VERSION})")

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def tx(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    # --- files ---------------------------------------------------------------------

    def get(self, folder_id: str, rel_path: str) -> FileRecord | None:
        row = self.db.execute(f"SELECT {', '.join(_FIELD_NAMES)} FROM files WHERE folder_id=? AND rel_path=?",
                              (folder_id, _enc(rel_path))).fetchone()
        return self._rec(row) if row else None

    def folder_records(self, folder_id: str) -> list[FileRecord]:
        rows = self.db.execute(f"SELECT {', '.join(_FIELD_NAMES)} FROM files WHERE folder_id=?", (folder_id,))
        return [self._rec(r) for r in rows]

    def all_records(self) -> list[FileRecord]:
        rows = self.db.execute(f"SELECT {', '.join(_FIELD_NAMES)} FROM files ORDER BY folder_id, rel_path")
        return [self._rec(r) for r in rows]

    def put(self, rec: FileRecord) -> None:
        rec = rec.with_(updated_at=time.time())
        values = [getattr(rec, n) for n in _FIELD_NAMES]
        values[1] = _enc(rec.rel_path)
        self.db.execute(f"INSERT OR REPLACE INTO files ({', '.join(_FIELD_NAMES)}) VALUES ({', '.join('?' * len(values))})",
                        values)

    def archive(self, folder_id: str, rel_path: str) -> None:
        """Copy the current record to files_history and delete it (records are never pruned, AR-08)."""
        cols = ", ".join(_FIELD_NAMES)
        key = (folder_id, _enc(rel_path))
        self.db.execute(f"INSERT INTO files_history ({cols}, archived_at) SELECT {cols}, ? FROM files "
                        "WHERE folder_id=? AND rel_path=?", (time.time(), *key))
        self.db.execute("DELETE FROM files WHERE folder_id=? AND rel_path=?", key)

    def delete(self, folder_id: str, rel_path: str) -> None:
        """Drop a record for a file that was never processed (vanished while settling)."""
        self.db.execute("DELETE FROM files WHERE folder_id=? AND rel_path=?", (folder_id, _enc(rel_path)))

    @staticmethod
    def _rec(row: tuple[object, ...]) -> FileRecord:
        values = list(row)
        values[1] = _dec(values[1])  # type: ignore[arg-type]
        return FileRecord(*values)  # type: ignore[arg-type]

    # --- action results ------------------------------------------------------------

    def action_results(self, folder_id: str, rel_path: str, source_key: str) -> dict[int, ActionRecord]:
        rows = self.db.execute(
            "SELECT action_index, action_type, ok, destination, dest_sha256, bytes, started, finished, error "
            "FROM action_results WHERE folder_id=? AND rel_path=? AND source_key=?",
            (folder_id, _enc(rel_path), source_key))
        out = {}
        for r in rows:
            out[r[0]] = ActionRecord(r[0], r[1], bool(r[2]), _dec(r[3]) if r[3] is not None else None, *r[4:])
        return out

    def put_action_result(self, folder_id: str, rel_path: str, source_key: str, rec: ActionRecord) -> None:
        dest = _enc(rec.destination) if rec.destination is not None else None
        self.db.execute(
            "INSERT OR REPLACE INTO action_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (folder_id, _enc(rel_path), source_key, rec.action_index, rec.action_type, int(rec.ok), dest,
             rec.dest_sha256, rec.bytes, rec.started, rec.finished, rec.error))

    # --- once-per-path notices -----------------------------------------------------

    def notice_once(self, folder_id: str, rel_path: str, kind: str) -> bool:
        """Record a notice. Returns True the first time, so the caller logs it once."""
        cur = self.db.execute("INSERT OR IGNORE INTO notices VALUES (?, ?, ?)", (folder_id, _enc(rel_path), kind))
        return cur.rowcount == 1

    def clear_notices(self, folder_id: str, kind: str, keep: set[str]) -> None:
        """Forget notices for paths that are gone, so they are logged again if they come back."""
        keep_raw = {_enc(p) for p in keep}
        rows = self.db.execute("SELECT rel_path FROM notices WHERE folder_id=? AND kind=?", (folder_id, kind)).fetchall()
        for (raw,) in rows:
            if raw not in keep_raw:
                self.db.execute("DELETE FROM notices WHERE folder_id=? AND rel_path=? AND kind=?", (folder_id, raw, kind))

    # --- recovery ------------------------------------------------------------------

    def recover(self) -> int:
        """At startup, move IN_PROGRESS records back to READY. Returns how many."""
        with self.tx():
            cur = self.db.execute("UPDATE files SET status='READY', updated_at=? WHERE status='IN_PROGRESS'", (time.time(),))
        return cur.rowcount


class LockBusy(Exception):
    """Another monfile instance holds the lock."""


class ProcessLock:
    """Non-blocking fcntl.flock on LOCK_PATH. Raises LockBusy if another instance holds it."""

    def __init__(self, path: Path = LOCK_PATH) -> None:
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            raise LockBusy(str(path)) from None

    def release(self) -> None:
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)
