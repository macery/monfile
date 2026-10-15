"""Enumerate candidate files in a watch folder (DESIGN §3, §4.3)."""

from __future__ import annotations

import fnmatch
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from monfile.config import FolderConfig


@dataclass(frozen=True)
class Candidate:
    rel_path: str
    dev: int
    ino: int
    size: int
    mtime_ns: int

    @property
    def stat_key(self) -> tuple[int, int, int, int]:
        return (self.dev, self.ino, self.size, self.mtime_ns)


@dataclass
class ScanResult:
    candidates: list[Candidate] = field(default_factory=list)
    markers: set[str] = field(default_factory=set)  # rel paths of ready-marker files
    symlinks: list[str] = field(default_factory=list)
    ignored: dict[str, str] = field(default_factory=dict)  # rel path → type, for non-regular entries
    errors: list[tuple[str, OSError]] = field(default_factory=list)  # unreadable subdirectories


def scan_folder(folder: FolderConfig) -> ScanResult:
    """List regular files that match the filters, using lstat only.

    Never follows symlinks, and honors folder.recursive. Ready markers are recognized
    before include/exclude filters, so a filter can never hide them (DESIGN §4.4).
    Raises OSError if the watch folder itself can't be read.
    """
    result = ScanResult()
    _scan_dir(folder, folder.path, "", result, top=True)
    result.candidates.sort(key=lambda c: c.rel_path)
    result.symlinks.sort()
    return result


def _scan_dir(folder: FolderConfig, path: Path, prefix: str, result: ScanResult, *, top: bool) -> None:
    try:
        with os.scandir(path) as it:
            entries = list(it)
    except OSError as exc:
        if top:
            raise
        result.errors.append((prefix.rstrip("/"), exc))
        return
    for entry in entries:
        rel = prefix + entry.name
        try:
            st = entry.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue  # vanished between listing and lstat
        if stat.S_ISLNK(st.st_mode):
            result.symlinks.append(rel)
        elif stat.S_ISDIR(st.st_mode):
            if not folder.recursive:
                result.ignored[rel] = "dir"
            elif not _matches_any(entry.name, folder.exclude):
                _scan_dir(folder, Path(entry.path), rel + "/", result, top=False)
        elif stat.S_ISREG(st.st_mode):
            if folder.ready_marker and entry.name.endswith(folder.ready_marker):
                result.markers.add(rel)
            elif matches_filters(entry.name, folder.include, folder.exclude):
                result.candidates.append(Candidate(rel, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns))
        else:
            result.ignored[rel] = _kind(st.st_mode)  # fifos, sockets, devices (DESIGN §4.3)


def _kind(mode: int) -> str:
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "chardev"
    if stat.S_ISBLK(mode):
        return "blockdev"
    return "other"


def _matches_any(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def matches_filters(name: str, include: tuple[str, ...], exclude: tuple[str, ...]) -> bool:
    """fnmatch the basename: it must match an include pattern and no exclude pattern."""
    return _matches_any(name, include) and not _matches_any(name, exclude)


def safe_join(root: Path, rel_path: str) -> Path:
    """Join rel_path under root, rejecting '..', absolute paths, and NUL (SR-07)."""
    if not rel_path or "\0" in rel_path or rel_path.startswith("/"):
        raise ValueError(f"unsafe relative path: {rel_path!r}")
    parts = rel_path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"unsafe relative path: {rel_path!r}")
    return root.joinpath(*parts)
