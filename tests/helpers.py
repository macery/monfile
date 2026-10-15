"""Shared test fixtures: temp folder trees and audit-event capture."""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from monfile import audit
from monfile.config import Config, parse_config
from monfile.state import StateStore


class EventCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())

    def events(self) -> list[str]:
        return [line.split(" ", 1)[0].removeprefix("event=") for line in self.lines]

    def find(self, event: str) -> list[str]:
        return [line for line in self.lines if line.startswith(f"event={event} ") or line == f"event={event}"]


class TreeCase(unittest.TestCase):
    """A temp dir with in/, out/, processed/, state/ and an event capture on the audit logger."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self._tmp.name))
        for d in ("in", "out", "processed", "state"):
            (self.root / d).mkdir()
        self.cap = EventCapture()
        audit.log.handlers[:] = [self.cap]
        audit.log.setLevel(logging.DEBUG)

    def tearDown(self) -> None:
        audit.log.handlers[:] = []
        self._tmp.cleanup()

    def config(self, folder: dict[str, Any] | None = None, **global_: Any) -> Config:
        f: dict[str, Any] = {
            "id": "t", "path": str(self.root / "in"), "settle_seconds": 2,
            "action": [{"type": "copy", "destination": str(self.root / "out")}],
        }
        f.update(folder or {})
        g = {"state_dir": str(self.root / "state"), "manifest_dir": str(self.root / "state" / "manifests"),
             "scan_interval_seconds": 1, **global_}
        gc, folders = parse_config({"global": g, "folder": [f]})
        return Config(gc, folders, self.root / "monfile.toml", "0" * 64)

    def store(self) -> StateStore:
        s = StateStore(self.root / "state" / "state.db")
        self.addCleanup(s.close)
        return s

    def write(self, rel: str, data: bytes = b"hello\n") -> Path:
        p = self.root / "in" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p
