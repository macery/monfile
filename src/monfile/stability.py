"""Per-file settle state machine (DESIGN §4, ADR-0002)."""

from __future__ import annotations

import enum

from monfile.config import FolderConfig
from monfile.scanner import Candidate


class Status(enum.StrEnum):
    SETTLING = "SETTLING"
    HASH_CHECK = "HASH_CHECK"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # skip_empty policy; a stat change makes it a new version


def evaluate(folder: FolderConfig, cand: Candidate, now: float) -> Status:
    """Advance the state record for `cand` and return its new status.

    Responsibilities:
    - reset the window on any stat change or a backwards clock
    - compute hash_start on the first unchanged scan
    - compute hash_end after settle_seconds, re-stat after hashing, compare hashes
    - when folder.ready_marker is set, hold READY until the marker exists (DESIGN §4.4)
    - mark settled zero-byte files SKIPPED when folder.skip_empty is set
    - emit FILE_DETECTED, FILE_CHANGED_DURING_SETTLE, FILE_READY, FILE_NEW_VERSION, MARKER_SEEN,
      FILE_SKIPPED, FILE_LARGE (> 10 GiB)
    """
    raise NotImplementedError
