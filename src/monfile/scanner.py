"""Enumerate candidate files in a watch folder (DESIGN §3, §4.3)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from monfile.config import FolderConfig


@dataclass(frozen=True)
class Candidate:
    rel_path: str
    dev: int
    ino: int
    size: int
    mtime_ns: int


def scan_folder(folder: FolderConfig) -> Iterator[Candidate]:
    """Yield regular files that match the filters, using lstat only.

    Never follows symlinks, and honors folder.recursive.
    """
    raise NotImplementedError


def matches_filters(name: str, include: tuple[str, ...], exclude: tuple[str, ...]) -> bool:
    """fnmatch the basename: it must match an include pattern and no exclude pattern."""
    raise NotImplementedError


def safe_join(root: Path, rel_path: str) -> Path:
    """Join rel_path under root, rejecting '..', absolute paths, and NUL (SR-07)."""
    raise NotImplementedError
