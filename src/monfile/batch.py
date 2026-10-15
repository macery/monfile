"""Folder batches: settle a whole folder, then run its command once (DESIGN §4.6, ADR-0011).

States per folder (table batch_folders):
  IDLE      folder empty, or the last run finished; waits for the listing to change
  SETTLING  listing seen at `since`; READY once unchanged for settle_seconds
  RUNNING   the command is running (at most one run per folder)
  PENDING   start as soon as retry_at passes and the listing is unchanged (busy exit, `monfile retry`)
  HELD      the last run failed; waits for the listing to change or `monfile retry`

The command runs as a child process while scan cycles continue. Each cycle polls it,
enforces the timeout, and records the outcome when it exits.
"""

from __future__ import annotations

import collections
import hashlib
import os
import secrets
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from monfile import __version__, audit
from monfile.actions.command import CommandAction, ProgramError, check_program, program_env
from monfile.audit import EventID
from monfile.config import FolderConfig, GlobalConfig
from monfile.scanner import Candidate, ScanResult, scan_folder
from monfile.stability import hash_candidate
from monfile.state import BatchFolder, BatchRecord, StateStore

IDLE, SETTLING, RUNNING, PENDING, HELD = "IDLE", "SETTLING", "RUNNING", "PENDING", "HELD"

KILL_GRACE = 30  # seconds from SIGTERM to SIGKILL (timeout and service stop)
BUSY_RETRY_SECONDS = 300  # after busy_exit_code, try again this much later
MAX_LINE_CHARS = 1000
MAX_OUTPUT_LINES = 10_000
MANIFEST_TAIL = 50
READ_CHUNK = 65536


def snapshot_digest(cands: list[Candidate]) -> str:
    """SHA-256 over the sorted listing (path, size, mtime, inode). "" for an empty folder."""
    if not cands:
        return ""
    h = hashlib.sha256()
    for c in cands:
        h.update(os.fsencode(c.rel_path) + b"\0" + f"{c.size}:{c.mtime_ns}:{c.ino}\n".encode())
    return h.hexdigest()


class _Output:
    """Reads the program's combined stdout/stderr on a thread and logs each line."""

    def __init__(self, folder: FolderConfig, batch_id: str, stream: IO[bytes]) -> None:
        self.folder, self.batch_id = folder, batch_id
        self.tail: collections.deque[str] = collections.deque(maxlen=MANIFEST_TAIL)
        self.count = 0
        self.thread = threading.Thread(target=self._read, args=(stream,), daemon=True,
                                       name=f"monfile-output-{folder.id}")
        self.thread.start()

    def _read(self, stream: IO[bytes]) -> None:
        try:
            for raw in iter(lambda: stream.readline(READ_CHUNK), b""):
                line = raw.rstrip(b"\r\n").decode("utf-8", "surrogateescape")[:MAX_LINE_CHARS]
                self.count += 1
                self.tail.append(line)
                if self.count <= MAX_OUTPUT_LINES:
                    audit.emit(EventID.COMMAND_OUTPUT, folder=self.folder.id, batch=self.batch_id, n=self.count,
                               line=line)
                elif self.count == MAX_OUTPUT_LINES + 1:
                    audit.emit(EventID.COMMAND_OUTPUT_TRUNCATED, folder=self.folder.id, batch=self.batch_id,
                               limit=MAX_OUTPUT_LINES)
        finally:
            stream.close()


@dataclass
class _Run:
    rec: BatchRecord
    action: CommandAction
    proc: subprocess.Popen[bytes]
    output: _Output
    started: float
    entries: list[tuple[str, int, int, str | None]]
    list_path: Path
    term_at: float | None = None
    killed: bool = False
    timed_out: bool = False


class BatchManager:
    def __init__(self, store: StateStore, g: GlobalConfig, *, dry_run: bool, allow_commands: bool,
                 stop: threading.Event) -> None:
        self.store, self.g = store, g
        self.dry_run = dry_run
        self.allow_commands = allow_commands
        self.stop = stop
        self.runs: dict[str, _Run] = {}
        self._mem: dict[str, BatchFolder] = {}  # dry runs keep state in memory only

    # --- state -----------------------------------------------------------------------

    def _get(self, folder_id: str) -> BatchFolder | None:
        if self.dry_run:
            return self._mem.get(folder_id)
        return self.store.get_batch_folder(folder_id)

    def _put(self, bf: BatchFolder) -> None:
        if self.dry_run:
            self._mem[bf.folder_id] = bf
            return
        with self.store.tx():
            self.store.put_batch_folder(bf)

    @property
    def list_dir(self) -> Path:
        return self.g.state_dir / "batches"

    # --- startup ---------------------------------------------------------------------

    def recover(self, now: float) -> None:
        """Runs that were RUNNING when monfile last stopped are logged as interrupted and settle again."""
        for bf in self.store.batch_folders():
            if bf.status != RUNNING:
                continue
            rec = self.store.get_batch(bf.batch_id) if bf.batch_id else None
            audit.emit(EventID.COMMAND_INTERRUPTED, folder=bf.folder_id, batch=bf.batch_id,
                       reason="monfile restarted", pid=rec.pid if rec else None)
            with self.store.tx():
                if rec is not None and rec.outcome is None:
                    self.store.put_batch(BatchRecord(**{**rec.__dict__, "outcome": "INTERRUPTED", "finished": now}))
                self.store.put_batch_folder(bf.with_(status=SETTLING, since=now))
        try:
            for p in self.list_dir.iterdir():
                if p.name.endswith(".list") and p.is_file():
                    p.unlink()
        except FileNotFoundError:
            pass

    # --- per cycle -------------------------------------------------------------------

    def poll(self, folder: FolderConfig, now: float) -> bool:
        """If the folder has a run in progress, check on it. Returns True if one was (or still is) running."""
        run = self.runs.get(folder.id)
        if run is None:
            return False
        if self.stop.is_set():
            return True  # stopping: systemd signals the program too; shutdown() records it as interrupted
        rc = run.proc.poll()
        if rc is not None:
            self._finish(folder, run, rc, now)
            return True
        if run.term_at is None and now - run.started >= run.action.timeout:
            run.timed_out = True
            run.term_at = now
            self._signal(run, signal.SIGTERM)
        elif run.term_at is not None and not run.killed and now - run.term_at >= KILL_GRACE:
            run.killed = True
            self._signal(run, signal.SIGKILL)
        return True

    def cycle(self, folder: FolderConfig, scan: ScanResult, now: float) -> None:
        if not self.allow_commands:
            audit.emit(EventID.COMMAND_REFUSED, folder=folder.id, path=folder.path,
                       program=folder.actions[0].params["program"],
                       error="trigger = \"folder\" needs the daemon (monfile.service), not scan --once")
            return
        cands = scan.candidates
        digest = snapshot_digest(cands)
        nbytes = sum(c.size for c in cands)
        bf = self._get(folder.id) or BatchFolder(folder.id, IDLE, "", now)
        if not cands:
            if bf.status != IDLE or bf.snapshot:
                self._put(BatchFolder(folder.id, IDLE, "", now, batch_id=bf.batch_id))
            return
        if bf.status == RUNNING:  # left by a run this process doesn't own (e.g. a dry run): settle again
            self._settle(folder, bf, digest, cands, now)
        elif bf.status in (IDLE, HELD):
            if digest != bf.snapshot:  # new or changed files since the last run
                self._settle(folder, bf, digest, cands, now)
        elif bf.status == PENDING:
            if digest != bf.snapshot:
                self._settle(folder, bf, digest, cands, now)
            elif bf.retry_at is None or now >= bf.retry_at:
                self._start(folder, bf, cands, now)
        elif digest != bf.snapshot:  # SETTLING, and something changed: restart the quiet window
            self._put(bf.with_(snapshot=digest, since=now, files=len(cands), bytes=nbytes))
        elif now - bf.since >= folder.settle_seconds:
            self._start(folder, bf, cands, now)

    def _settle(self, folder: FolderConfig, bf: BatchFolder, digest: str, cands: list[Candidate], now: float) -> None:
        nbytes = sum(c.size for c in cands)
        audit.emit(EventID.BATCH_SETTLING, folder=folder.id, path=folder.path, files=len(cands), bytes=nbytes)
        self._put(bf.with_(status=SETTLING, snapshot=digest, since=now, files=len(cands), bytes=nbytes, retry_at=None))

    # --- start -----------------------------------------------------------------------

    def _start(self, folder: FolderConfig, bf: BatchFolder, cands: list[Candidate], now: float) -> None:
        action = CommandAction(folder.actions[0].params)
        nbytes = sum(c.size for c in cands)
        try:
            program_sha = check_program(action.program)
        except ProgramError as exc:
            audit.emit(EventID.COMMAND_REFUSED, folder=folder.id, path=folder.path, program=action.program,
                       error=str(exc))
            self._put(bf.with_(status=HELD, since=now))
            return

        entries: list[tuple[str, int, int, str | None]] = []
        for c in cands:
            sha = None
            if folder.hash_batch:
                if self.stop.is_set():
                    return
                try:
                    sha = hash_candidate(folder, c, self.g.hash_chunk_bytes)
                except FileNotFoundError:
                    sha = None
                except OSError as exc:
                    audit.emit(EventID.PERMISSION_DENIED, folder=folder.id, path=folder.path / c.rel_path, op="read",
                               error=exc.strerror or str(exc))
                    self._put(bf.with_(status=HELD, since=now))
                    return
                if sha is None:  # changed while hashing: not settled after all
                    self._put(bf.with_(status=SETTLING, since=now))
                    return
            entries.append((c.rel_path, c.size, c.mtime_ns, sha))

        batch_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now)) + "-" + secrets.token_hex(3)
        audit.emit(EventID.BATCH_READY, folder=folder.id, path=folder.path, batch=batch_id, files=len(cands),
                   bytes=nbytes, snapshot=bf.snapshot, settled_seconds=int(now - bf.since))
        if self.dry_run:
            audit.emit(EventID.COMMAND_START, folder=folder.id, path=folder.path, dry_run=True, batch=batch_id,
                       program=action.program, sha256=program_sha)
            self._put(bf.with_(status=HELD, since=now))
            return

        rec = BatchRecord(batch_id, folder.id, bf.snapshot, len(cands), nbytes, str(action.program), program_sha,
                          started=now)
        list_path = self._write_list(batch_id, [e[0] for e in entries])
        try:
            proc = subprocess.Popen(
                [str(action.program), *action.args], shell=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=folder.path, close_fds=True,
                start_new_session=True, env=program_env(folder.id, folder.path, batch_id, list_path),
            )
        except OSError as exc:
            error = exc.strerror or str(exc)
            audit.emit(EventID.COMMAND_FAIL, folder=folder.id, path=folder.path, batch=batch_id, exit_code=None,
                       error=error)
            with self.store.tx():
                self.store.put_batch(BatchRecord(**{**rec.__dict__, "finished": now, "outcome": "FAIL", "error": error}))
                self.store.put_batch_files(batch_id, entries)
                self.store.put_batch_folder(bf.with_(status=HELD, since=now, batch_id=batch_id))
            list_path.unlink(missing_ok=True)
            return
        rec = BatchRecord(**{**rec.__dict__, "pid": proc.pid})
        with self.store.tx():
            self.store.put_batch(rec)
            self.store.put_batch_files(batch_id, entries)
            self.store.put_batch_folder(bf.with_(status=RUNNING, since=now, batch_id=batch_id, retry_at=None))
        audit.emit(EventID.COMMAND_START, folder=folder.id, path=folder.path, batch=batch_id, program=action.program,
                   sha256=program_sha, pid=proc.pid, files=len(cands), timeout_seconds=action.timeout)
        assert proc.stdout is not None
        output = _Output(folder, batch_id, proc.stdout)
        self.runs[folder.id] = _Run(rec, action, proc, output, now, entries, list_path)

    def _write_list(self, batch_id: str, rel_paths: list[str]) -> Path:
        """MONFILE_BATCH_LIST: the batch's relative paths, each terminated by NUL (names may contain newlines)."""
        self.list_dir.mkdir(mode=0o750, exist_ok=True)
        path = self.list_dir / f"{batch_id}.list"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o640)
        try:
            os.write(fd, b"".join(os.fsencode(p) + b"\0" for p in rel_paths))
        finally:
            os.close(fd)
        return path

    # --- finish ----------------------------------------------------------------------

    @staticmethod
    def _signal(run: _Run, sig: signal.Signals) -> None:
        try:
            os.killpg(run.proc.pid, sig)  # its own session: reaches everything it started
        except (ProcessLookupError, PermissionError):
            pass

    def _finish(self, folder: FolderConfig, run: _Run, rc: int, now: float) -> None:
        del self.runs[folder.id]
        run.output.thread.join(5)  # a leftover child holding the pipe must not hang the service
        duration_ms = int((now - run.started) * 1000)
        try:
            after = scan_folder(folder).candidates
            remaining: int | None = len(after)
            post = snapshot_digest(after)
            files, nbytes = len(after), sum(c.size for c in after)
        except OSError:
            remaining, post, files, nbytes = None, run.rec.snapshot, run.rec.files, run.rec.bytes
        bid = run.rec.batch_id
        keys: dict[str, Any] = {"batch": bid, "exit_code": rc, "duration_ms": duration_ms,
                                "output_lines": run.output.count}
        error = None
        bf = BatchFolder(folder.id, HELD, post, now, files, nbytes, batch_id=bid)
        if run.timed_out:
            outcome, error = "TIMEOUT", f"timed out after {run.action.timeout} s"
        elif run.action.busy_exit_code is not None and rc == run.action.busy_exit_code:
            outcome = "BUSY"
            bf = bf.with_(status=PENDING, retry_at=now + BUSY_RETRY_SECONDS)
        elif rc == 0 and (not run.action.expect_empty or remaining == 0):
            outcome = "DONE"
            bf = bf.with_(status=IDLE)
        elif rc == 0:
            outcome, error = "INCOMPLETE", f"{remaining if remaining is not None else 'unknown'} file(s) left"
        else:
            outcome = "FAIL"
            error = f"killed by {signal.Signals(-rc).name}" if rc < 0 else f"exit code {rc}"

        manifest = None if outcome == "BUSY" else self._manifest(folder, run, rc, outcome, now, remaining)
        if outcome == "TIMEOUT":
            audit.emit(EventID.COMMAND_TIMEOUT, folder=folder.id, path=folder.path, **keys,
                       timeout_seconds=run.action.timeout, manifest=manifest)
        elif outcome == "BUSY":
            audit.emit(EventID.COMMAND_BUSY, folder=folder.id, path=folder.path, **keys, retry_in=BUSY_RETRY_SECONDS)
        elif outcome == "DONE":
            audit.emit(EventID.BATCH_DONE, folder=folder.id, path=folder.path, **keys, files=run.rec.files,
                       remaining=remaining, manifest=manifest)
        elif outcome == "INCOMPLETE":
            audit.emit(EventID.BATCH_INCOMPLETE, folder=folder.id, path=folder.path, **keys, remaining=remaining,
                       manifest=manifest)
        else:
            audit.emit(EventID.COMMAND_FAIL, folder=folder.id, path=folder.path, **keys, error=error,
                       manifest=manifest)
        with self.store.tx():
            self.store.put_batch(BatchRecord(**{**run.rec.__dict__, "finished": now, "exit_code": rc,
                                                "outcome": outcome, "output_lines": run.output.count,
                                                "error": error}))
            self.store.put_batch_folder(bf)
        run.list_path.unlink(missing_ok=True)

    def _manifest(self, folder: FolderConfig, run: _Run, rc: int | None, outcome: str, now: float,
                  remaining: int | None) -> str | None:
        if not folder.manifest:
            return None
        record: dict[str, Any] = {
            "schema_version": 1,
            "kind": "batch",
            "monfile_version": __version__,
            "host": socket.gethostname(),
            "folder_id": folder.id,
            "batch": {
                "id": run.rec.batch_id, "path": str(folder.path), "snapshot": run.rec.snapshot,
                "files": [{"path": p, "size": size, "mtime": audit.utc_iso(mtime / 1e9), "sha256": sha}
                          for p, size, mtime, sha in run.entries],
                "bytes": run.rec.bytes,
            },
            "command": {
                "program": run.rec.program, "sha256": run.rec.program_sha256, "args": list(run.action.args),
                "pid": run.proc.pid, "started": audit.utc_iso(run.started), "finished": audit.utc_iso(now),
                "duration_ms": int((now - run.started) * 1000), "timeout_seconds": run.action.timeout,
                "exit_code": rc, "output_lines": run.output.count, "output_tail": list(run.output.tail),
            },
            "remaining_files": remaining,
            "status": outcome,
        }
        try:
            return str(audit.write_manifest(self.g.manifest_dir, folder.id, record, now=now,
                                            label=(run.rec.snapshot or "nohash", f"batch-{run.rec.batch_id}")))
        except OSError as exc:
            audit.emit(EventID.MANIFEST_WRITE_FAIL, folder=folder.id, path=folder.path, error=exc.strerror or str(exc))
            return None

    # --- shutdown --------------------------------------------------------------------

    def shutdown(self, reason: str) -> None:
        """Stop running programs (SIGTERM, then SIGKILL after KILL_GRACE). They settle again on the next start."""
        for folder_id, run in list(self.runs.items()):
            self._signal(run, signal.SIGTERM)
            try:
                run.proc.wait(KILL_GRACE)
            except subprocess.TimeoutExpired:
                self._signal(run, signal.SIGKILL)
                run.proc.wait()
            run.output.thread.join(5)
            now = time.time()
            audit.emit(EventID.COMMAND_INTERRUPTED, folder=folder_id, batch=run.rec.batch_id, reason=reason,
                       pid=run.proc.pid, duration_ms=int((now - run.started) * 1000), output_lines=run.output.count)
            with self.store.tx():
                self.store.put_batch(BatchRecord(**{**run.rec.__dict__, "finished": now, "exit_code": run.proc.returncode,
                                                    "outcome": "INTERRUPTED", "output_lines": run.output.count,
                                                    "error": reason}))
                self.store.put_batch_folder(BatchFolder(folder_id, SETTLING, run.rec.snapshot, now, run.rec.files,
                                                        run.rec.bytes, batch_id=run.rec.batch_id))
            run.list_path.unlink(missing_ok=True)
            del self.runs[folder_id]
