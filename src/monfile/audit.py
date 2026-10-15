"""Structured audit events to syslog and per-file manifests (DESIGN §10).

EventID must stay in sync with docs/audit/AUDIT_EVENTS.md. A test enforces this.
"""

from __future__ import annotations

import enum
import json
import logging
import logging.handlers
import os
import secrets
import sys
import time
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
    FILE_IGNORED_NONREGULAR = "FILE_IGNORED_NONREGULAR"
    FILE_HARDLINK_DUPLICATE = "FILE_HARDLINK_DUPLICATE"
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


NOTICE = 25
logging.addLevelName(NOTICE, "NOTICE")

_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "notice": NOTICE, "warning": logging.WARNING, "error": logging.ERROR}

# Severity of each event, as listed in the catalog. A test checks the two agree.
SEVERITY: dict[EventID, str] = {
    EventID.SERVICE_START: "info",
    EventID.SERVICE_STOP: "info",
    EventID.CONFIG_LOADED: "info",
    EventID.CONFIG_INVALID: "error",
    EventID.CONFIG_PERMS_INSECURE: "error",
    EventID.LOCK_BUSY: "info",
    EventID.FOLDER_UNAVAILABLE: "warning",
    EventID.FILE_DETECTED: "info",
    EventID.FILE_CHANGED_DURING_SETTLE: "info",
    EventID.FILE_VANISHED: "debug",
    EventID.FILE_READY: "info",
    EventID.FILE_SKIPPED: "info",
    EventID.FILE_LARGE: "warning",
    EventID.FILE_NEW_VERSION: "info",
    EventID.SYMLINK_REJECTED: "warning",
    EventID.FILE_IGNORED_NONREGULAR: "debug",
    EventID.FILE_HARDLINK_DUPLICATE: "info",
    EventID.MARKER_SEEN: "info",
    EventID.MARKER_ORPHAN: "warning",
    EventID.MARKER_REMOVED: "info",
    EventID.MARKER_REMOVE_FAIL: "warning",
    EventID.PERMISSION_DENIED: "error",
    EventID.ACTION_START: "debug",
    EventID.ACTION_OK: "info",
    EventID.ACTION_SKIPPED_DONE: "info",
    EventID.ACTION_FAIL: "error",
    EventID.INTEGRITY_MISMATCH: "error",
    EventID.COLLISION_RESOLVED: "info",
    EventID.DISPOSITION_OK: "info",
    EventID.DISPOSITION_FAIL: "error",
    EventID.FILE_DONE: "info",
    EventID.MANIFEST_WRITE_FAIL: "warning",
    EventID.RETRY_REQUESTED: "notice",
    EventID.TEMP_CLEANUP: "info",
    EventID.DRY_RUN: "info",
}

# Keys whose values are always quoted (paths and free text).
_QUOTED_KEYS = {"path", "same_as", "dest", "target", "marker", "config", "error", "manifest", "lock", "python"}

log = logging.getLogger("monfile")
log.propagate = False

_SD_PRIORITY = {logging.DEBUG: 7, logging.INFO: 6, NOTICE: 5, logging.WARNING: 4, logging.ERROR: 3}


class _JournalStderrFormatter(logging.Formatter):
    """Prefix '<N>' so journald keeps the severity when we log via stderr."""

    def format(self, record: logging.LogRecord) -> str:
        return f"<{_SD_PRIORITY.get(record.levelno, 6)}>{record.getMessage()}"


def setup_logging(facility: str, level: str, *, stderr: bool | None = None) -> None:
    """Configure a SysLogHandler on /dev/log with ident 'monfile'.

    Falls back to stderr (with journald priority prefixes) when /dev/log is
    unavailable. Also logs to stderr when it is a terminal, for manual runs.
    """
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    log.setLevel(_LEVELS[level])
    syslog_ok = False
    try:
        handler = logging.handlers.SysLogHandler(
            address="/dev/log", facility=logging.handlers.SysLogHandler.facility_names[facility]
        )
        handler.priority_map = {**handler.priority_map, "NOTICE": "notice"}
        handler.setFormatter(logging.Formatter("monfile[%(process)d]: %(message)s"))
        log.addHandler(handler)
        syslog_ok = True
    except OSError:
        pass
    tty = sys.stderr.isatty()
    if stderr or (stderr is None and (tty or not syslog_ok)):
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter("%(message)s") if tty else _JournalStderrFormatter())
        log.addHandler(sh)


def escape(value: str) -> str:
    """Escape quotes, backslashes, control characters, and undecodable bytes."""
    out: list[str] = []
    for ch in value:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif o < 0x20 or o == 0x7F:
            out.append(f"\\x{o:02x}")
        elif 0xDC80 <= o <= 0xDCFF:  # surrogateescape'd byte from a non-UTF-8 filename
            out.append(f"\\x{o - 0xDC00:02x}")
        else:
            out.append(ch)
    return "".join(out)


def _fmt_value(key: str, value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value)
    if key in _QUOTED_KEYS or not s or any(c in s for c in ' "=\\') or not s.isprintable():
        return f'"{escape(s)}"'
    return s


def format_event(event: EventID | str, *, folder: str | None = None, path: str | os.PathLike[str] | None = None,
                 **fields: Any) -> str:
    parts = [f"event={event}"]
    if folder is not None:
        parts.append(f"folder={_fmt_value('folder', folder)}")
    if path is not None:
        parts.append(f"path={_fmt_value('path', os.fspath(path))}")
    for k, v in fields.items():
        parts.append(f"{k}={_fmt_value(k, v)}")
    return " ".join(parts)


def emit(event: EventID, *, folder: str | None = None, path: str | os.PathLike[str] | None = None,
         dry_run: bool = False, **fields: Any) -> None:
    """Log 'event=<ID> folder=.. path="..." k=v'. Escapes control chars and never logs file contents.

    With dry_run=True, the event is logged as DRY_RUN with would=<ID>.
    """
    level = _LEVELS[SEVERITY[event]]
    if dry_run:
        fields = {"would": str(event), **fields}
        event = EventID.DRY_RUN
    log.log(level, format_event(event, folder=folder, path=path, **fields))


def utc_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + f".{int(ts % 1 * 1000):03d}Z"


def _safe_name(name: str, limit: int = 100) -> str:
    cleaned = "".join(c if c.isprintable() and c not in "/\\" and not 0xDC80 <= ord(c) <= 0xDCFF else "_" for c in name)
    return cleaned[:limit] or "_"


def write_manifest(manifest_dir: Path, folder_id: str, record: dict[str, Any], *, now: float | None = None) -> Path:
    """Write the manifest JSON atomically. Layout is in DESIGN §10.2."""
    now = time.time() if now is None else now
    t = time.gmtime(now)
    day_dir = manifest_dir / folder_id / f"{t.tm_year:04d}" / f"{t.tm_mon:02d}" / f"{t.tm_mday:02d}"
    day_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
    sha = (record.get("source", {}).get("sha256") or "nohash")[:12]
    base = _safe_name(os.path.basename(record.get("source", {}).get("path", "file")))
    name = f"{time.strftime('%Y%m%dT%H%M%S', t)}{int(now % 1 * 1000):03d}Z_{sha}_{base}.json"
    final = day_dir / name
    tmp = day_dir / f".monfile-tmp-{secrets.token_hex(8)}"
    data = json.dumps(record, indent=2, ensure_ascii=True, default=str).encode() + b"\n"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o640)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, final)
    dfd = os.open(day_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return final
