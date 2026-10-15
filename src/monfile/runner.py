"""Scan cycles, startup recovery, and the daemon loop (DESIGN §3, §8, §11)."""

from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path

from monfile import audit
from monfile.actions.deliver import TEMP_PREFIX
from monfile.audit import EventID
from monfile.config import Config, FolderConfig, GlobalConfig
from monfile.executor import process_file
from monfile.scanner import Candidate, scan_folder
from monfile.stability import TERMINAL, Status, evaluate
from monfile.state import StateStore

TEMP_MAX_AGE = 3600  # stale temp files older than this are removed at startup (DESIGN §8)


class Runner:
    def __init__(self, cfg: Config, store: StateStore, *, dry_run: bool = False,
                 stop: threading.Event | None = None) -> None:
        self.cfg = cfg
        self.g: GlobalConfig = cfg.global_
        self.store = store
        self.dry_run = dry_run
        self.stop = stop or threading.Event()

    # --- startup ---------------------------------------------------------------------

    def startup(self, now: float | None = None) -> None:
        self.store.recover()
        if not self.dry_run:
            self.cleanup_temp_files(time.time() if now is None else now)

    def cleanup_temp_files(self, now: float) -> None:
        uid = os.geteuid()
        roots = {root for f in self.cfg.folders for root in f.output_roots()}
        roots.add(self.g.manifest_dir)
        for root in sorted(roots):
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                for name in filenames:
                    if not name.startswith(TEMP_PREFIX):
                        continue
                    p = Path(dirpath) / name
                    try:
                        st = os.lstat(p)
                        if stat.S_ISREG(st.st_mode) and st.st_uid == uid and now - st.st_mtime > TEMP_MAX_AGE:
                            os.unlink(p)
                            audit.emit(EventID.TEMP_CLEANUP, dest=p)
                    except OSError:
                        continue

    # --- one cycle -------------------------------------------------------------------

    def run_cycle(self, now: float | None = None) -> None:
        for folder in self.cfg.folders:
            if self.stop.is_set():
                return
            self.run_folder(folder, time.time() if now is None else now)

    def run_folder(self, folder: FolderConfig, now: float) -> None:
        try:
            scan = scan_folder(folder)
        except OSError as exc:
            audit.emit(EventID.FOLDER_UNAVAILABLE, folder=folder.id, path=folder.path, error=exc.strerror or str(exc))
            return
        store = self.store
        for rel, exc in scan.errors:
            if store.notice_once(folder.id, rel, "unreadable_dir"):
                audit.emit(EventID.PERMISSION_DENIED, folder=folder.id, path=folder.path / rel, op="scandir",
                           error=exc.strerror or str(exc))
        store.clear_notices(folder.id, "unreadable_dir", {rel for rel, _ in scan.errors})
        for rel in scan.symlinks:
            if store.notice_once(folder.id, rel, "symlink"):
                audit.emit(EventID.SYMLINK_REJECTED, folder=folder.id, path=folder.path / rel)
        store.clear_notices(folder.id, "symlink", set(scan.symlinks))
        for rel, kind in sorted(scan.ignored.items()):
            if store.notice_once(folder.id, rel, "nonregular"):
                audit.emit(EventID.FILE_IGNORED_NONREGULAR, folder=folder.id, path=folder.path / rel, type=kind)
        store.clear_notices(folder.id, "nonregular", set(scan.ignored))
        self._hardlinks(folder, scan.candidates)

        present = {c.rel_path for c in scan.candidates}
        if folder.ready_marker:
            n = len(folder.ready_marker)
            orphans = {m for m in scan.markers if m[:-n] not in present}
            for m in sorted(orphans):
                if store.notice_once(folder.id, m, "marker_orphan"):
                    audit.emit(EventID.MARKER_ORPHAN, folder=folder.id, path=folder.path / m[:-n],
                               marker=folder.path / m)
            store.clear_notices(folder.id, "marker_orphan", orphans)

        self._vanished(folder, present)

        budget = self.g.max_files_per_cycle
        for cand in scan.candidates:
            if self.stop.is_set() or budget <= 0:
                break
            rec = store.get(folder.id, cand.rel_path)
            if rec is not None and Status(rec.status) in TERMINAL and not rec.gone and rec.stat_key == cand.stat_key:
                continue  # already handled and unchanged: free
            budget -= 1
            marker = bool(folder.ready_marker) and (cand.rel_path + (folder.ready_marker or "")) in scan.markers
            try:
                with store.tx():
                    status = evaluate(store, folder, cand, now, marker_present=marker, chunk=self.g.hash_chunk_bytes)
            except FileNotFoundError:
                continue  # vanished mid-check; next scan drops it
            except OSError as exc:
                self._unreadable(folder, cand.rel_path, exc)
                continue
            if status == Status.READY:
                rec = store.get(folder.id, cand.rel_path)
                assert rec is not None
                process_file(store, self.g, folder, rec, dry_run=self.dry_run)

    def _hardlinks(self, folder: FolderConfig, candidates: list[Candidate]) -> None:
        """Log once per path when two candidates are hard links to the same inode (DESIGN §4.3)."""
        by_inode: dict[tuple[int, int], list[str]] = {}
        for c in candidates:
            by_inode.setdefault((c.dev, c.ino), []).append(c.rel_path)
        linked: set[str] = set()
        for (_, ino), paths in by_inode.items():
            if len(paths) < 2:
                continue
            linked.update(paths)
            for rel in paths[1:]:
                if self.store.notice_once(folder.id, rel, "hardlink"):
                    audit.emit(EventID.FILE_HARDLINK_DUPLICATE, folder=folder.id, path=folder.path / rel,
                               inode=ino, same_as=folder.path / paths[0])
        self.store.clear_notices(folder.id, "hardlink", linked)

    def _vanished(self, folder: FolderConfig, present: set[str]) -> None:
        with self.store.tx():
            for rec in self.store.folder_records(folder.id):
                if rec.rel_path in present or rec.gone:
                    continue
                status = Status(rec.status)
                if status in (Status.DONE, Status.SKIPPED):
                    self.store.put(rec.with_(gone=1))  # removed by someone else after processing: keep record
                    continue
                audit.emit(EventID.FILE_VANISHED, folder=folder.id, path=folder.path / rec.rel_path)
                if status == Status.FAILED:
                    self.store.put(rec.with_(gone=1))
                else:
                    self.store.delete(folder.id, rec.rel_path)  # never processed: nothing to keep

    def _unreadable(self, folder: FolderConfig, rel: str, exc: OSError) -> None:
        src = folder.path / rel
        error = exc.strerror or str(exc)
        if isinstance(exc, PermissionError):
            audit.emit(EventID.PERMISSION_DENIED, folder=folder.id, path=src, op="read", error=error)
        rec = self.store.get(folder.id, rel)
        if rec is not None:
            self.store.put(rec.with_(status=Status.FAILED, last_error=error))

    # --- daemon ----------------------------------------------------------------------

    def run_forever(self) -> None:
        interval = self.g.scan_interval_seconds
        while not self.stop.is_set():
            started = time.monotonic()
            self.run_cycle()
            self.stop.wait(max(0.0, interval - (time.monotonic() - started)))
