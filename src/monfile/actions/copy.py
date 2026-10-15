"""copy action: atomic, SHA-256-verified copy to a destination (DESIGN §6.3)."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from monfile import audit
from monfile.actions import Action, ActionContext, ActionResult
from monfile.actions.deliver import DeliveryError, IntegrityError, copy_into, open_subdirs, resolve_name
from monfile.audit import EventID
from monfile.config import ConfigError
from monfile.hashing import open_regular

COLLISION_POLICIES = ("suffix", "skip", "fail")


class CopyAction(Action):
    """Params: destination (abs path), collision (suffix|skip|fail), preserve_tree (bool)."""

    type_name = "copy"
    allowed_params = {"destination": str, "collision": str, "preserve_tree": bool}
    required_params = frozenset({"destination"})

    def __init__(self, params: Mapping[str, Any]) -> None:
        super().__init__(params)
        self.destination = Path(self.params["destination"])
        self.collision = self.params.get("collision", "suffix")
        if self.collision not in COLLISION_POLICIES:
            raise ConfigError(f"collision must be one of {', '.join(COLLISION_POLICIES)}")
        self.preserve_tree = self.params.get("preserve_tree", False)

    # --- helpers shared with MoveAction ------------------------------------------

    def target_parts(self, ctx: ActionContext) -> tuple[list[str], str]:
        parts = ctx.rel_path.split("/")
        subdirs = parts[:-1] if (self.preserve_tree and ctx.folder.recursive) else []
        return subdirs, parts[-1]

    def source_dir_fd(self, ctx: ActionContext) -> int:
        """Open the source's directory under the watch path without following symlinks."""
        return open_subdirs(ctx.folder.path, ctx.rel_path.split("/")[:-1], create=False)

    def check_source(self, ctx: ActionContext, st: os.stat_result) -> None:
        now = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        if now != ctx.source_stat:
            raise DeliveryError("source changed after it settled")

    def emit_collision(self, ctx: ActionContext, kind: str | None, dest: Path) -> None:
        if kind is not None:
            audit.emit(EventID.COLLISION_RESOLVED, folder=ctx.folder.id, path=ctx.source,
                       dry_run=ctx.dry_run, policy=kind if kind == "identical" else self.collision, dest=dest)

    def fail(self, ctx: ActionContext, exc: BaseException, started: float, op: str) -> ActionResult:
        if isinstance(exc, IntegrityError):
            audit.emit(EventID.INTEGRITY_MISMATCH, folder=ctx.folder.id, path=ctx.source,
                       stage=exc.stage, expected=exc.expected, actual=exc.actual)
        elif isinstance(exc, PermissionError):
            audit.emit(EventID.PERMISSION_DENIED, folder=ctx.folder.id, path=ctx.source, op=op, error=_err(exc))
        return ActionResult(ok=False, started=started, finished=time.time(), error=_err(exc))

    def dry_run(self, ctx: ActionContext, started: float) -> ActionResult:
        subdirs, name = self.target_parts(ctx)
        try:
            dir_fd = open_subdirs(self.destination, subdirs, create=False)
        except FileNotFoundError:
            dest = self.destination.joinpath(*subdirs, name)  # would be created
            return ActionResult(ok=True, destination=dest, started=started, finished=time.time())
        try:
            placement = resolve_name(dir_fd, name, self.collision, ctx.source_sha256, ctx.chunk_bytes)
        finally:
            os.close(dir_fd)
        dest = self.destination.joinpath(*subdirs, placement.name)
        self.emit_collision(ctx, placement.collision, dest)
        return ActionResult(ok=True, destination=dest, started=started, finished=time.time())

    # --- action ----------------------------------------------------------------------

    def run(self, ctx: ActionContext) -> ActionResult:
        started = time.time()
        if ctx.dry_run:
            try:
                return self.dry_run(ctx, started)
            except (OSError, DeliveryError) as exc:
                return self.fail(ctx, exc, started, "check_destination")
        subdirs, name = self.target_parts(ctx)
        op = "open_source"
        try:
            src_dir_fd = self.source_dir_fd(ctx)
            try:
                src_fd = open_regular(ctx.source.name, dir_fd=src_dir_fd)
            finally:
                os.close(src_dir_fd)
            try:
                self.check_source(ctx, os.fstat(src_fd))
                op = "write_destination"
                dir_fd = open_subdirs(self.destination, subdirs, create=True)
                try:
                    d = copy_into(src_fd, dir_fd, name, expected_sha=ctx.source_sha256,
                                  policy=self.collision, chunk=ctx.chunk_bytes)
                finally:
                    os.close(dir_fd)
                self.check_source(ctx, os.fstat(src_fd))  # unchanged while we copied
            finally:
                os.close(src_fd)
        except (OSError, DeliveryError) as exc:
            return self.fail(ctx, exc, started, op)
        dest = self.destination.joinpath(*subdirs, d.name)
        self.emit_collision(ctx, d.collision, dest)
        return ActionResult(ok=True, destination=dest, dest_sha256=d.sha256, bytes=d.bytes,
                            started=started, finished=time.time())


def _err(exc: BaseException) -> str:
    if isinstance(exc, OSError) and exc.strerror:
        return f"{exc.strerror}: {exc.filename}" if exc.filename else exc.strerror
    return str(exc) or type(exc).__name__

