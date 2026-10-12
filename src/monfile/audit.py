"""Structured audit events to syslog and per-file manifests (DESIGN §10).

EventID must stay in sync with docs/audit/AUDIT_EVENTS.md. A test enforces this.
"""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Any


class EventID(enum.StrEnum):
    SERVICE_START = "SERVICE_START"
    SERVICE_STOP = "SERVICE_STOP"
    CONFIG_LOADED = "CONFIG_LOADED"
    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_PERMS_INSECURE = "CONFIG_PERMS_INSECURE"
    LOCK_BUSY = "LOCK_BUSY"
    FOLDER_UNAVAILABLE = "FOLDER_UNAVAILABLE"
    FILE_DETECTED = "FILE_DETECTED"
    FILE_CHANGED_DURING_SETTLE = "FILE_CHANGED_DURING_SETTLE"
    FILE_VANISHED = "FILE_VANISHED"
    FILE_READY = "FILE_READY"
    FILE_SKIPPED = "FILE_SKIPPED"
    FILE_LARGE = "FILE_LARGE"
    FILE_NEW_VERSION = "FILE_NEW_VERSION"
    SYMLINK_REJECTED = "SYMLINK_REJECTED"
    MARKER_SEEN = "MARKER_SEEN"
    MARKER_ORPHAN = "MARKER_ORPHAN"
    MARKER_REMOVED = "MARKER_REMOVED"
    MARKER_REMOVE_FAIL = "MARKER_REMOVE_FAIL"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    ACTION_START = "ACTION_START"
    ACTION_OK = "ACTION_OK"
    ACTION_SKIPPED_DONE = "ACTION_SKIPPED_DONE"
    ACTION_FAIL = "ACTION_FAIL"
    INTEGRITY_MISMATCH = "INTEGRITY_MISMATCH"
    COLLISION_RESOLVED = "COLLISION_RESOLVED"
    DISPOSITION_OK = "DISPOSITION_OK"
    DISPOSITION_FAIL = "DISPOSITION_FAIL"
    FILE_DONE = "FILE_DONE"
    MANIFEST_WRITE_FAIL = "MANIFEST_WRITE_FAIL"
    RETRY_REQUESTED = "RETRY_REQUESTED"
    TEMP_CLEANUP = "TEMP_CLEANUP"
    DRY_RUN = "DRY_RUN"


def setup_logging(facility: str, level: str) -> None:
    """Configure a SysLogHandler on /dev/log with ident 'monfile'."""
    raise NotImplementedError


def emit(event: EventID, *, folder: str | None = None, path: str | None = None, **fields: Any) -> None:
    """Log 'event=<ID> folder=.. path="..." k=v'. Escapes control chars and never logs file contents."""
    raise NotImplementedError


def write_manifest(manifest_dir: Path, folder_id: str, record: dict[str, Any]) -> Path:
    """Write the manifest JSON atomically. Layout is in DESIGN §10.2."""
    raise NotImplementedError
