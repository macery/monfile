"""Run a READY file's action chain, then apply its disposition (DESIGN §6.2, §7)."""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from typing import Any

from monfile import __version__, audit
from monfile.actions import ActionContext, ActionResult, get_action
from monfile.actions.deliver import DeliveryError, open_subdirs
from monfile.actions.move import MoveAction, unlink_source
from monfile.audit import EventID
from monfile.config import FolderConfig, GlobalConfig
from monfile.stability import Status
from monfile.state import ActionRecord, FileRecord, StateStore


def source_key(rec: FileRecord) -> str:
    """Key for per-action results: the settled SHA-256, or the stat tuple when verify_hash is off."""
    return rec.hash_end or f"stat:{rec.dev}:{rec.ino}:{rec.size}:{rec.mtime_ns}"


def process_file(store: StateStore, g: GlobalConfig, folder: FolderConfig, rec: FileRecord, *, dry_run: bool) -> bool:
    """Run each action in order and skip actions already done for this hash.

    Stops at the first failure and marks the file FAILED (ADR-0005). On full
    success, applies on_success and writes the manifest if enabled.
    Returns True if the file reached DONE.
    """
    src = folder.path / rec.rel_path
    key = source_key(rec)
    if not dry_run:
        rec = rec.with_(status=Status.IN_PROGRESS, attempts=rec.attempts + 1)
        store.put(rec)
    done = {} if dry_run else {i: r for i, r in store.action_results(folder.id, rec.rel_path, key).items() if r.ok}
    results: list[ActionResult] = []
    manifest_actions: list[dict[str, Any]] = []

    for index, ac in enumerate(folder.actions):
        prior = done.get(index)
        if prior is not None and prior.action_type == ac.type:
            audit.emit(EventID.ACTION_SKIPPED_DONE, folder=folder.id, path=src, action=ac.type, index=index)
            result = ActionResult(ok=True, destination=Path(prior.destination) if prior.destination else None,
                                  dest_sha256=prior.dest_sha256, bytes=prior.bytes, started=prior.started,
                                  finished=prior.finished)
        else:
            audit.emit(EventID.ACTION_START, folder=folder.id, path=src, action=ac.type, index=index)
            ctx = ActionContext(folder=folder, source=src, rel_path=rec.rel_path, source_sha256=rec.hash_end,
                                source_stat=rec.stat_key, dry_run=dry_run, chunk_bytes=g.hash_chunk_bytes,
                                previous=tuple(results))
            try:
                result = get_action(ac.type)(ac.params).run(ctx)
            except Exception as exc:  # an action bug must not take down the service
                result = ActionResult(ok=False, started=time.time(), finished=time.time(),
                                      error=f"internal error: {type(exc).__name__}: {exc}")
            if not dry_run:
                store.put_action_result(folder.id, rec.rel_path, key, ActionRecord(
                    index, ac.type, result.ok, str(result.destination) if result.destination else None,
                    result.dest_sha256, result.bytes, result.started, result.finished, result.error))
            if result.ok:
                audit.emit(EventID.ACTION_OK, folder=folder.id, path=src, dry_run=dry_run, action=ac.type, index=index,
                           dest=result.destination, sha256=result.dest_sha256, bytes=result.bytes,
                           duration_ms=int((result.finished - result.started) * 1000))
            else:
                audit.emit(EventID.ACTION_FAIL, folder=folder.id, path=src, action=ac.type, index=index,
                           error=result.error)
        results.append(result)
        manifest_actions.append(_manifest_action(index, ac.type, result))
        if not result.ok:
            if not dry_run:
                store.put(rec.with_(status=Status.FAILED, last_error=result.error))
                _manifest(store, g, folder, rec, manifest_actions, None, None, Status.FAILED)
            return False

    disposition = _dispose(g, folder, rec, src, dry_run=dry_run)
    if dry_run:
        return False
    if not disposition["ok"]:
        store.put(rec.with_(status=Status.FAILED, last_error=disposition["error"]))
        _manifest(store, g, folder, rec, manifest_actions, disposition, None, Status.FAILED)
        return False

    marker = _remove_marker(folder, rec, src)
    store.put(rec.with_(status=Status.DONE, gone=int(folder.consumes_source), last_error=None))
    manifest_path = _manifest(store, g, folder, rec, manifest_actions, disposition, marker, Status.DONE)
    audit.emit(EventID.FILE_DONE, folder=folder.id, path=src, sha256=rec.hash_end or _last_sha(results),
               actions=len(results), manifest=manifest_path)
    return True


def _last_sha(results: list[ActionResult]) -> str | None:
    return next((r.dest_sha256 for r in reversed(results) if r.dest_sha256), None)


def _dispose(g: GlobalConfig, folder: FolderConfig, rec: FileRecord, src: Path, *, dry_run: bool) -> dict[str, Any]:
    mode = folder.on_success
    if any(a.type == "move" for a in folder.actions):
        mode = "leave"  # the move action already took the source (config enforces on_success = leave)
    target: Path | None = src
    error = None
    ctx = ActionContext(folder=folder, source=src, rel_path=rec.rel_path, source_sha256=rec.hash_end,
                        source_stat=rec.stat_key, dry_run=dry_run, chunk_bytes=g.hash_chunk_bytes)
    try:
        if mode == "move":
            assert folder.processed_dir is not None
            same_fs = os.stat(folder.processed_dir).st_dev == rec.dev
            mover = MoveAction({"destination": str(folder.processed_dir), "collision": "suffix",
                                "preserve_tree": True, "mode": "rename" if same_fs else "copy"})
            mover.rename_fallback = True  # same st_dev can still be different mounts (EXDEV)
            result = mover.run(ctx)
            target, error = result.destination, result.error
        elif mode == "delete":
            target = None
            if not dry_run:
                unlink_source(ctx, open_subdirs(folder.path, rec.rel_path.split("/")[:-1], create=False))
    except (OSError, DeliveryError) as exc:
        error = f"{exc.strerror}: {exc.filename}" if isinstance(exc, OSError) and exc.strerror else str(exc)
    if error:
        audit.emit(EventID.DISPOSITION_FAIL, folder=folder.id, path=src, mode=mode, error=error)
        return {"mode": mode, "target": None, "ok": False, "error": error}
    audit.emit(EventID.DISPOSITION_OK, folder=folder.id, path=src, dry_run=dry_run, mode=mode, target=target)
    return {"mode": mode, "target": str(target) if target else None, "ok": True, "error": None}


def _remove_marker(folder: FolderConfig, rec: FileRecord, src: Path) -> dict[str, Any] | None:
    """Unlink the ready marker after success (DESIGN §4.4). A failure is logged, and the file stays DONE."""
    if not folder.ready_marker:
        return None
    parts = rec.rel_path.split("/")
    name = parts[-1] + folder.ready_marker
    marker_path = str(src) + folder.ready_marker
    info: dict[str, Any] = {"path": marker_path, "seen_at": audit.utc_iso(rec.marker_seen_at) if rec.marker_seen_at else None,
                            "removed": False}
    try:
        dir_fd = open_subdirs(folder.path, parts[:-1], create=False)
        try:
            st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if (st.st_mode & 0o170000) != 0o100000:
                raise OSError(f"marker is not a regular file: {marker_path}")
            os.unlink(name, dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        error = exc.strerror or str(exc)
        audit.emit(EventID.MARKER_REMOVE_FAIL, folder=folder.id, path=src, marker=marker_path, error=error)
        return info
    info["removed"] = True
    audit.emit(EventID.MARKER_REMOVED, folder=folder.id, path=src, marker=marker_path)
    return info


def _manifest_action(index: int, type_: str, r: ActionResult) -> dict[str, Any]:
    return {"index": index, "type": type_, "destination": str(r.destination) if r.destination else None,
            "ok": r.ok, "dest_sha256": r.dest_sha256, "verified": r.ok, "bytes": r.bytes,
            "started": audit.utc_iso(r.started) if r.started else None,
            "finished": audit.utc_iso(r.finished) if r.finished else None, "error": r.error}


def _manifest(store: StateStore, g: GlobalConfig, folder: FolderConfig, rec: FileRecord, actions: list[dict[str, Any]],
              disposition: dict[str, Any] | None, marker: dict[str, Any] | None, status: Status) -> str | None:
    if not folder.manifest:
        return None
    src = folder.path / rec.rel_path
    record: dict[str, Any] = {
        "schema_version": 1,
        "monfile_version": __version__,
        "host": socket.gethostname(),
        "folder_id": folder.id,
        "source": {"path": str(src), "size": rec.size, "mtime": audit.utc_iso(rec.mtime_ns / 1e9),
                   "sha256": rec.hash_end or next((a["dest_sha256"] for a in actions if a["dest_sha256"]), None),
                   "first_seen": audit.utc_iso(rec.first_seen), "ready_at": audit.utc_iso(rec.updated_at)},
        "actions": actions,
        "disposition": disposition,
        "status": str(status),
    }
    if marker is not None:
        record["marker"] = marker
    try:
        return str(audit.write_manifest(g.manifest_dir, folder.id, record))
    except OSError as exc:
        audit.emit(EventID.MANIFEST_WRITE_FAIL, folder=folder.id, path=src, error=exc.strerror or str(exc))
        return None
