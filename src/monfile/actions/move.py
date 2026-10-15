"""move action: copy + verify + unlink source (default), or same-fs rename (DESIGN §6.3)."""

from __future__ import annotations

import errno
import os
import time
from collections.abc import Mapping
from typing import Any

from monfile.actions import ActionContext, ActionResult
from monfile.actions.copy import CopyAction
from monfile.actions.deliver import DeliveryError, open_subdirs, rename_into
from monfile.config import ConfigError

MOVE_MODES = ("copy", "rename")


class MoveAction(CopyAction):
    """Params: as for copy, plus mode ("copy" default | "rename"). Must be last in the chain."""

    type_name = "move"
    consumes_source = True
    allowed_params = {**CopyAction.allowed_params, "mode": str}
    # Set by the processed_dir disposition only: on EXDEV, fall back to copy + verify + unlink.
    rename_fallback = False

    def __init__(self, params: Mapping[str, Any]) -> None:
        super().__init__(params)
        self.mode = self.params.get("mode", "copy")
        if self.mode not in MOVE_MODES:
            raise ConfigError(f"mode must be one of {', '.join(MOVE_MODES)}")

    def run(self, ctx: ActionContext) -> ActionResult:
        if ctx.dry_run:
            return super().run(ctx)
        if self.mode == "copy":
            return self._copy_then_unlink(ctx)
        return self._rename(ctx)

    def _copy_then_unlink(self, ctx: ActionContext) -> ActionResult:
        result = super().run(ctx)
        if not result.ok:
            return result
        try:
            unlink_source(ctx, self.source_dir_fd(ctx))
        except (OSError, DeliveryError) as exc:
            failed = self.fail(ctx, exc, result.started, "unlink_source")
            return ActionResult(ok=False, destination=result.destination, dest_sha256=result.dest_sha256,
                                bytes=result.bytes, started=result.started, finished=failed.finished,
                                error=f"delivered, but the source could not be removed: {failed.error}")
        return result

    def _rename(self, ctx: ActionContext) -> ActionResult:
        started = time.time()
        subdirs, name = self.target_parts(ctx)
        try:
            src_dir_fd = self.source_dir_fd(ctx)
            try:
                self.check_source(ctx, os.stat(ctx.source.name, dir_fd=src_dir_fd, follow_symlinks=False))
                dir_fd = open_subdirs(self.destination, subdirs, create=True)
                try:
                    d = rename_into(src_dir_fd, ctx.source.name, dir_fd, name, expected_sha=ctx.source_sha256,
                                    policy=self.collision, chunk=ctx.chunk_bytes)
                finally:
                    os.close(dir_fd)
            finally:
                os.close(src_dir_fd)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                return self.fail(ctx, exc, started, "rename")
            # Different filesystems, or different mount points of the same one: systemd makes each
            # ReadWritePaths= entry its own bind mount, and rename() can't cross mount points.
            if self.rename_fallback:
                return self._copy_then_unlink(ctx)
            exc.strerror = ("cannot rename across mount points (each ReadWritePaths= entry is a separate mount); "
                            "use mode = \"copy\", or list a common parent directory in ReadWritePaths=")
            return self.fail(ctx, exc, started, "rename")
        except DeliveryError as exc:
            return self.fail(ctx, exc, started, "rename")
        dest = self.destination.joinpath(*subdirs, d.name)
        self.emit_collision(ctx, d.collision, dest)
        return ActionResult(ok=True, destination=dest, dest_sha256=d.sha256, bytes=d.bytes,
                            started=started, finished=time.time())


def unlink_source(ctx: ActionContext, dir_fd: int) -> None:
    """Remove the source, but only if it is still the file that settled and was delivered. Closes dir_fd."""
    try:
        st = os.stat(ctx.source.name, dir_fd=dir_fd, follow_symlinks=False)
        if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) != ctx.source_stat:
            raise DeliveryError("source changed after delivery; left in place")
        os.unlink(ctx.source.name, dir_fd=dir_fd)
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
