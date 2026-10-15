"""Chunked SHA-256 hashing (ADR-0007). FIPS-approved algorithms only."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

DEFAULT_CHUNK = 1024 * 1024
NOFOLLOW_RDONLY = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


def open_regular(path: Path | str, *, dir_fd: int | None = None) -> int:
    """Open a regular file read-only without following symlinks. Returns the fd."""
    fd = os.open(path, NOFOLLOW_RDONLY, dir_fd=dir_fd)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"not a regular file: {path}")
    return fd


def sha256_fd(fd: int, chunk_bytes: int = DEFAULT_CHUNK) -> tuple[str, int]:
    """Hash an open fd from offset 0. Returns (hex digest, bytes read)."""
    h = hashlib.sha256()
    total = 0
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, chunk_bytes):
        h.update(chunk)
        total += len(chunk)
    return h.hexdigest(), total


def sha256_file(path: Path | str, chunk_bytes: int = DEFAULT_CHUNK, *, dir_fd: int | None = None) -> str:
    """Return the hex SHA-256 of the file, opened with O_NOFOLLOW."""
    fd = open_regular(path, dir_fd=dir_fd)
    try:
        return sha256_fd(fd, chunk_bytes)[0]
    finally:
        os.close(fd)
