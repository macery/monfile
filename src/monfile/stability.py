"""Per-file settle state machine (DESIGN §4, ADR-0002)."""

from __future__ import annotations

import enum
import os

from monfile import audit
from monfile.actions.deliver import open_subdirs
from monfile.audit import EventID
from monfile.config import FolderConfig
from monfile.hashing import open_regular, sha256_fd
from monfile.scanner import Candidate
from monfile.state import FileRecord, StateStore

LARGE_FILE_BYTES = 10 * 1024**3  # §13 Q3: warn, don't refuse


class Status(enum.StrEnum):
    SETTLING = "SETTLING"
    HASH_CHECK = "HASH_CHECK"  # transient: never persisted
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # skip_empty policy; a stat change makes it a new version


TERMINAL = {Status.DONE, Status.FAILED, Status.SKIPPED}


def hash_candidate(folder: FolderConfig, cand: Candidate, chunk: int) -> str | None:
    """Hash the file, then re-check its stat on the same fd. Returns None if it changed meanwhile."""
    parts = cand.rel_path.split("/")
    dir_fd = open_subdirs(folder.path, parts[:-1], create=False)
    try:
        fd = open_regular(parts[-1], dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    try:
        digest, _ = sha256_fd(fd, chunk)
        st = os.fstat(fd)
    finally:
        os.close(fd)
    if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) != cand.stat_key:
        return None
    return digest


def _fresh(folder: FolderConfig, cand: Candidate, now: float) -> FileRecord:
    return FileRecord(folder.id, cand.rel_path, *cand.stat_key, first_seen=now, stable_since=now,
                      hash_start=None, hash_end=None, marker_seen_at=None, status=Status.SETTLING)


def evaluate(store: StateStore, folder: FolderConfig, cand: Candidate, now: float, *,
             marker_present: bool = False, chunk: int = 1024 * 1024) -> Status:
    """Advance the state record for `cand` and return its new status.

    Responsibilities:
    - reset the window on any stat change or a backwards clock
    - compute hash_start on the first unchanged scan
    - compute hash_end after settle_seconds, re-stat after hashing, compare hashes
    - when folder.ready_marker is set, hold READY until the marker exists (DESIGN §4.4)
    - mark settled zero-byte files SKIPPED when folder.skip_empty is set
    - emit FILE_DETECTED, FILE_CHANGED_DURING_SETTLE, FILE_READY, FILE_NEW_VERSION, MARKER_SEEN,
      FILE_SKIPPED, FILE_LARGE (> 10 GiB)
    """
    rec = store.get(folder.id, cand.rel_path)
    src = folder.path / cand.rel_path

    if rec is None or (rec.gone and Status(rec.status) in TERMINAL):
        # A brand-new path, or a new file reusing the name of one already moved/deleted.
        if rec is not None:
            store.archive(folder.id, cand.rel_path)
        store.put(_fresh(folder, cand, now))
        audit.emit(EventID.FILE_DETECTED, folder=folder.id, path=src, size=cand.size, mtime=cand.mtime_ns)
        return Status.SETTLING

    status = Status(rec.status)
    if status in TERMINAL:
        if rec.stat_key == cand.stat_key:
            return status
        audit.emit(EventID.FILE_NEW_VERSION, folder=folder.id, path=src, old_sha256=rec.hash_end)
        store.archive(folder.id, cand.rel_path)
        store.put(_fresh(folder, cand, now))
        return Status.SETTLING

    if rec.stat_key != cand.stat_key or now < rec.stable_since:
        reason = "stat" if rec.stat_key != cand.stat_key else "clock"
        store.put(rec.with_(dev=cand.dev, ino=cand.ino, size=cand.size, mtime_ns=cand.mtime_ns, stable_since=now,
                            hash_start=None, hash_end=None, status=Status.SETTLING))
        audit.emit(EventID.FILE_CHANGED_DURING_SETTLE, folder=folder.id, path=src, reason=reason)
        return Status.SETTLING

    if status == Status.READY:
        return status  # waiting for the executor (e.g. after a retry or a restart)

    if folder.ready_marker:
        if marker_present and rec.marker_seen_at is None:
            rec = rec.with_(marker_seen_at=now)
            audit.emit(EventID.MARKER_SEEN, folder=folder.id, path=src, marker=str(src) + folder.ready_marker)
        elif not marker_present and rec.marker_seen_at is not None:
            rec = rec.with_(marker_seen_at=None)

    if folder.verify_hash and rec.hash_start is None:
        digest = hash_candidate(folder, cand, chunk)
        if digest is None:
            return _changed(store, rec, folder, src, now, "stat")
        rec = rec.with_(hash_start=digest)

    window_done = now - rec.stable_since >= folder.settle_seconds
    marker_ok = not folder.ready_marker or rec.marker_seen_at is not None
    if not (window_done and marker_ok):
        store.put(rec)
        return Status.SETTLING

    # HASH_CHECK
    hash_end = None
    if folder.verify_hash:
        hash_end = hash_candidate(folder, cand, chunk)
        if hash_end is None:
            return _changed(store, rec, folder, src, now, "stat")
        if hash_end != rec.hash_start:
            store.put(rec.with_(hash_start=hash_end, stable_since=now))
            audit.emit(EventID.FILE_CHANGED_DURING_SETTLE, folder=folder.id, path=src, reason="hash")
            return Status.SETTLING

    settled = int(now - rec.stable_since)
    if cand.size == 0 and folder.skip_empty:
        store.put(rec.with_(hash_end=hash_end, status=Status.SKIPPED))
        audit.emit(EventID.FILE_SKIPPED, folder=folder.id, path=src, reason="empty")
        return Status.SKIPPED

    store.put(rec.with_(hash_end=hash_end, status=Status.READY))
    audit.emit(EventID.FILE_READY, folder=folder.id, path=src, size=cand.size, sha256=hash_end, settled_seconds=settled)
    if cand.size > LARGE_FILE_BYTES:
        audit.emit(EventID.FILE_LARGE, folder=folder.id, path=src, size=cand.size)
    return Status.READY


def _changed(store: StateStore, rec: FileRecord, folder: FolderConfig, src: object, now: float, reason: str) -> Status:
    store.put(rec.with_(stable_since=now, hash_start=None, hash_end=None, status=Status.SETTLING))
    audit.emit(EventID.FILE_CHANGED_DURING_SETTLE, folder=folder.id, path=str(src), reason=reason)
    return Status.SETTLING
