"""Run a READY file's action chain, then apply its disposition (DESIGN §6.2, §7)."""

from __future__ import annotations

from monfile.config import FolderConfig


def process_file(folder: FolderConfig, rel_path: str, source_sha256: str | None, *, dry_run: bool) -> bool:
    """Run each action in order and skip actions already done for this hash.

    Stops at the first failure and marks the file FAILED (ADR-0005). On full
    success, applies on_success and writes the manifest if enabled.
    Returns True if the file reached DONE.
    """
    raise NotImplementedError
