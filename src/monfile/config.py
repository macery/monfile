"""Configuration loading and validation (DESIGN §5, ADR-0004)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Disposition = Literal["leave", "move", "delete"]
Collision = Literal["suffix", "skip", "fail"]


class ConfigError(Exception):
    """Invalid config contents or insecure config permissions."""


@dataclass(frozen=True)
class ActionConfig:
    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FolderConfig:
    id: str
    path: Path
    recursive: bool = False
    settle_seconds: int = 300
    verify_hash: bool = True
    skip_empty: bool = False
    manifest: bool = False
    include: tuple[str, ...] = ("*",)
    exclude: tuple[str, ...] = (".*", "*.part", "*.tmp", "~$*", "*.crdownload", "*.filepart")
    on_success: Disposition = "leave"
    processed_dir: Path | None = None
    ready_marker: str | None = None  # e.g. ".done"; None = settle-only (DESIGN §4.4)
    actions: tuple[ActionConfig, ...] = ()


@dataclass(frozen=True)
class GlobalConfig:
    state_dir: Path = Path("/var/lib/monfile")
    manifest_dir: Path = Path("/var/lib/monfile/manifests")
    scan_interval_seconds: int = 30
    log_level: str = "info"
    syslog_facility: str = "daemon"
    hash_chunk_bytes: int = 1024 * 1024
    max_files_per_cycle: int = 1000


@dataclass(frozen=True)
class Config:
    global_: GlobalConfig
    folders: tuple[FolderConfig, ...]
    source_path: Path
    sha256: str  # hash of the raw config bytes, logged in CONFIG_LOADED


def check_permissions(path: Path) -> None:
    """Raise ConfigError unless the file is root-owned and not group/world-writable (SR-06)."""
    raise NotImplementedError


def load_config(path: Path) -> Config:
    """Read with tomllib, check permissions, and validate per the DESIGN §5 rules.

    Unknown keys are errors.
    """
    raise NotImplementedError
