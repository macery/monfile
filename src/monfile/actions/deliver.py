"""Atomic, verified delivery of one file into a directory (DESIGN §6.3, §7, SR-08, SR-09).

All directory access goes through O_NOFOLLOW directory fds, so a symlink swapped
into a destination tree can't redirect a write (SR-04).
"""

from __future__ import annotations

import errno
import hashlib
import os
import secrets
import stat
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from monfile.hashing import open_regular, sha256_fd

DELIVERED_MODE = 0o640  # §13 Q4
TEMP_PREFIX = ".monfile-tmp-"
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class DeliveryError(Exception):
    """Delivery failed; nothing was left under the final name."""


class IntegrityError(DeliveryError):
    def __init__(self, stage: str, expected: str, actual: str) -> None:
        super().__init__(f"SHA-256 mismatch at {stage}: expected {expected}, got {actual}")
        self.stage, self.expected, self.actual = stage, expected, actual


class CollisionError(DeliveryError):
    pass


@dataclass(frozen=True)
class Placement:
    name: str
    collision: str | None  # None, "identical", "suffix", "skip"


def open_dir(path: Path) -> int:
    return os.open(path, DIR_FLAGS)


def open_subdirs(root: Path, parts: Sequence[str], *, create: bool) -> int:
    """Open root/parts... one component at a time without following symlinks. Returns a dir fd."""
    fd = open_dir(root)
    try:
        for part in parts:
            if part in ("", ".", "..") or "/" in part:
                raise DeliveryError(f"unsafe path component: {part!r}")
            if create:
                try:
                    os.mkdir(part, 0o770, dir_fd=fd)  # UMask=0027 → 0750; inherits setgid group
                except FileExistsError:
                    pass
            nfd = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _exists(dir_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def suffixed_name(name: str, now: float, n: int) -> str:
    """report.pdf → report.20261015T120000Z.pdf, then report.20261015T120000Z-2.pdf, ..."""
    stem, ext = os.path.splitext(name)
    if not stem:  # ".bashrc"-style name: treat the whole thing as the stem
        stem, ext = name, ""
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    return f"{stem}.{ts}{'' if n == 1 else f'-{n}'}{ext}"


def resolve_name(dir_fd: int, name: str, policy: str, source_sha: str | None, chunk: int) -> Placement:
    """Pick the final name. An existing file with identical content counts as already delivered."""
    st = _exists(dir_fd, name)
    if st is None:
        return Placement(name, None)
    if source_sha and stat.S_ISREG(st.st_mode):
        try:
            existing = sha256_fd_at(dir_fd, name, chunk)
        except OSError:
            existing = None
        if existing == source_sha:
            return Placement(name, "identical")
    if policy == "suffix":
        now = time.time()
        for n in range(1, 10_000):
            candidate = suffixed_name(name, now, n)
            if _exists(dir_fd, candidate) is None:
                return Placement(candidate, "suffix")
        raise CollisionError(f"no free suffixed name for {name}")
    if policy == "skip":
        raise CollisionError(f"{name} exists with different content (collision = skip)")
    raise CollisionError(f"{name} already exists (collision = fail)")


def sha256_fd_at(dir_fd: int, name: str, chunk: int) -> str:
    fd = open_regular(name, dir_fd=dir_fd)
    try:
        return sha256_fd(fd, chunk)[0]
    finally:
        os.close(fd)


def _fsync_dir(dir_fd: int) -> None:
    os.fsync(dir_fd)


@dataclass(frozen=True)
class Delivered:
    name: str
    sha256: str
    bytes: int
    collision: str | None


def copy_into(src_fd: int, dir_fd: int, name: str, *, expected_sha: str | None, policy: str, chunk: int) -> Delivered:
    """Copy src_fd into dir_fd under name (or a collision-resolved name), atomically and verified.

    1. If the target exists with identical content, return without writing.
    2. Stream to a 0600 temp file, hashing as we go; compare to expected_sha; fsync.
    3. Set mode 0640, re-read the temp file and verify its SHA-256.
    4. Hard-link the temp file to the final name (never replaces an existing file), unlink the temp, fsync the dir.
    """
    if expected_sha is None and _exists(dir_fd, name) is not None:
        expected_sha = sha256_fd(src_fd, chunk)[0]  # needed to recognize an identical existing file
    placement = resolve_name(dir_fd, name, policy, expected_sha, chunk)
    if placement.collision == "identical":
        assert expected_sha is not None
        size = os.fstat(src_fd).st_size
        return Delivered(placement.name, expected_sha, size, "identical")

    tmp = f"{TEMP_PREFIX}{secrets.token_hex(8)}"
    out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dir_fd)
    linked = False
    try:
        h = hashlib.sha256()
        total = 0
        os.lseek(src_fd, 0, os.SEEK_SET)
        try:
            while buf := os.read(src_fd, chunk):
                h.update(buf)
                view = memoryview(buf)
                while view:
                    view = view[os.write(out, view):]
                total += len(buf)
            os.fsync(out)
            os.fchmod(out, DELIVERED_MODE)
        finally:
            os.close(out)
        stream_sha = h.hexdigest()
        if expected_sha is not None and stream_sha != expected_sha:
            raise IntegrityError("stream", expected_sha, stream_sha)
        written_sha = sha256_fd_at(dir_fd, tmp, chunk)
        if written_sha != stream_sha:
            raise IntegrityError("dest", stream_sha, written_sha)

        while True:
            try:
                os.link(tmp, placement.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
                linked = True
                break
            except FileExistsError:  # appeared since we checked; pick again
                placement = resolve_name(dir_fd, name, policy, stream_sha, chunk)
                if placement.collision == "identical":
                    break
            except OSError as exc:
                if exc.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.EMLINK):
                    raise
                # Filesystem without hard links: check-then-rename (small race window).
                placement = resolve_name(dir_fd, name, policy, stream_sha, chunk)
                if placement.collision != "identical":
                    os.rename(tmp, placement.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                    tmp = ""
                break
        _fsync_dir(dir_fd)
        return Delivered(placement.name, stream_sha, total, placement.collision)
    finally:
        if tmp:
            try:
                os.unlink(tmp, dir_fd=dir_fd)
                if linked:
                    _fsync_dir(dir_fd)
            except FileNotFoundError:
                pass


def rename_into(src_dir_fd: int, src_name: str, dir_fd: int, name: str, *, expected_sha: str | None, policy: str,
                chunk: int) -> Delivered:
    """Same-filesystem rename with collision handling, then verify the result by SHA-256."""
    src_fd = open_regular(src_name, dir_fd=src_dir_fd)
    try:
        if expected_sha is None:
            expected_sha = sha256_fd(src_fd, chunk)[0]
        size = os.fstat(src_fd).st_size
    finally:
        os.close(src_fd)
    placement = resolve_name(dir_fd, name, policy, expected_sha, chunk)
    if placement.collision != "identical":
        if _exists(dir_fd, placement.name) is not None:
            raise CollisionError(f"{placement.name} appeared during rename")
        os.rename(src_name, placement.name, src_dir_fd=src_dir_fd, dst_dir_fd=dir_fd)
        _fsync_dir(dir_fd)
        _fsync_dir(src_dir_fd)
    actual = sha256_fd_at(dir_fd, placement.name, chunk)
    if actual != expected_sha:
        raise IntegrityError("dest", expected_sha, actual)
    if placement.collision == "identical":
        os.unlink(src_name, dir_fd=src_dir_fd)
        _fsync_dir(src_dir_fd)
    return Delivered(placement.name, actual, size, placement.collision)
