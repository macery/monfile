"""command action: run one approved program per settled folder batch (DESIGN §6.4, ADR-0011).

The program is run without a shell, with fixed arguments, by monfile.batch. This module
validates the config and checks the program before every run.
"""

from __future__ import annotations

import os
import pwd
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from monfile.actions import Action, ActionContext, ActionResult
from monfile.config import ConfigError
from monfile.hashing import open_regular, sha256_fd

DEFAULT_TIMEOUT = 7200
MAX_TIMEOUT = 7 * 24 * 3600
SAFE_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"

# Who may own the program and every directory above it. Tests widen these; nothing else may.
TRUSTED_OWNERS: frozenset[int] = frozenset({0})
TRUST_ROOT = Path("/")  # directories are checked from the program up to and including this one


class ProgramError(Exception):
    """The program failed an ownership, mode, or type check (COMMAND_REFUSED)."""


class CommandAction(Action):
    """Params: program (abs path), args (list of str), timeout_seconds, busy_exit_code, expect_empty."""

    type_name = "command"
    allowed_params: ClassVar[dict[str, type]] = {
        "program": str, "args": list, "timeout_seconds": int, "busy_exit_code": int, "expect_empty": bool,
    }
    required_params = frozenset({"program"})

    def __init__(self, params: Mapping[str, Any]) -> None:
        super().__init__(params)
        program = self.params["program"]
        if not program.startswith("/") or os.path.normpath(program) != program or "\0" in program:
            raise ConfigError(f"program must be an absolute, normalized path: {program!r}")
        self.program = Path(program)
        self.args: tuple[str, ...] = tuple(self.params.get("args", []))
        if not all(isinstance(a, str) and "\0" not in a for a in self.args):
            raise ConfigError("args must be a list of strings")
        self.timeout = self.params.get("timeout_seconds", DEFAULT_TIMEOUT)
        if not 1 <= self.timeout <= MAX_TIMEOUT:
            raise ConfigError(f"timeout_seconds must be between 1 and {MAX_TIMEOUT}")
        self.busy_exit_code: int | None = self.params.get("busy_exit_code")
        if self.busy_exit_code is not None and not 1 <= self.busy_exit_code <= 255:
            raise ConfigError("busy_exit_code must be between 1 and 255")
        self.expect_empty: bool = self.params.get("expect_empty", True)

    def run(self, ctx: ActionContext) -> ActionResult:
        # Config validation only allows this action with trigger = "folder", which never calls run().
        return ActionResult(ok=False, error="the command action runs per folder batch, not per file")


def check_program(path: Path) -> str:
    """Check the program and return its SHA-256. Raises ProgramError (SR-13).

    The program must be a regular, executable file owned by a trusted owner and not group- or
    world-writable, with no symlink anywhere in its path. Every directory above it must be a
    trusted-owner directory that is not group- or world-writable, so only root can swap it.
    """
    if os.path.realpath(path) != str(path):
        raise ProgramError(f"{path}: path contains a symlink")
    for d in _dirs_above(path):
        try:
            st = os.lstat(d)
        except OSError as exc:
            raise ProgramError(f"{d}: {exc.strerror}") from None
        if not stat.S_ISDIR(st.st_mode):
            raise ProgramError(f"{d}: not a directory")
        _check_owner_mode(d, st, "directory")
    try:
        fd = open_regular(path)
    except OSError as exc:
        raise ProgramError(f"{path}: {exc.strerror or exc}") from None
    try:
        st = os.fstat(fd)
        _check_owner_mode(path, st, "program")
        if not st.st_mode & stat.S_IXUSR:
            raise ProgramError(f"{path}: not executable")
        return sha256_fd(fd)[0]
    finally:
        os.close(fd)


def _dirs_above(path: Path) -> list[Path]:
    out = []
    for d in path.parents:
        out.append(d)
        if d == TRUST_ROOT:
            break
    return out


def _check_owner_mode(path: Path, st: os.stat_result, what: str) -> None:
    if st.st_uid not in TRUSTED_OWNERS:
        raise ProgramError(f"{path}: {what} must be owned by root (owner uid {st.st_uid})")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ProgramError(f"{path}: {what} must not be group- or world-writable "
                           f"(mode {stat.S_IMODE(st.st_mode):04o})")


def program_env(folder_id: str, folder_path: Path, batch_id: str, batch_list: Path) -> dict[str, str]:
    """The program's whole environment: fixed, minimal, and never inherited from monfile."""
    pw = pwd.getpwuid(os.geteuid())
    return {
        "PATH": SAFE_PATH,
        "HOME": pw.pw_dir,
        "USER": pw.pw_name,
        "LOGNAME": pw.pw_name,
        "LANG": "C.UTF-8",
        "MONFILE_FOLDER_ID": folder_id,
        "MONFILE_FOLDER_PATH": str(folder_path),
        "MONFILE_BATCH_ID": batch_id,
        "MONFILE_BATCH_LIST": str(batch_list),
    }
