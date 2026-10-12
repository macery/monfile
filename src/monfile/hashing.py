"""Chunked SHA-256 hashing (ADR-0007). FIPS-approved algorithms only."""

from __future__ import annotations

from pathlib import Path


def sha256_file(path: Path, chunk_bytes: int = 1024 * 1024) -> str:
    """Return the hex SHA-256 of the file, opened with O_NOFOLLOW."""
    raise NotImplementedError
