"""Action plugin interface and registry (DESIGN §6.1, ADR-0006).

To add an action: subclass Action, set type_name, and add it to _registry().
Actions must be idempotent and honor ctx.dry_run.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from monfile.config import ConfigError, FolderConfig


@dataclass(frozen=True)
class ActionContext:
    folder: FolderConfig
    source: Path
    rel_path: str
    source_sha256: str | None
    source_stat: tuple[int, int, int, int]  # (dev, ino, size, mtime_ns) when the file settled
    dry_run: bool
    chunk_bytes: int = 1024 * 1024
    previous: tuple[ActionResult, ...] = ()


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    destination: Path | None = None
    dest_sha256: str | None = None
    bytes: int = 0
    started: float = 0.0
    finished: float = 0.0
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Action(ABC):
    type_name: ClassVar[str]
    consumes_source: ClassVar[bool] = False  # True means it must be last in the chain (DESIGN §5)
    allowed_params: ClassVar[dict[str, type]] = {}
    required_params: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, params: Mapping[str, Any]) -> None:
        """Validate params at config load time. Raise ConfigError on bad input."""
        for key in sorted(set(params) - set(self.allowed_params)):
            raise ConfigError(f"unknown key {key!r} for action {self.type_name!r}")
        for key in sorted(self.required_params - set(params)):
            raise ConfigError(f"{key} is required for action {self.type_name!r}")
        for key, value in params.items():
            kind = self.allowed_params[key]
            if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
                raise ConfigError(f"{key} must be {kind.__name__}, got {value!r}")
        self.params = dict(params)

    @abstractmethod
    def run(self, ctx: ActionContext) -> ActionResult: ...


def _registry() -> dict[str, type[Action]]:
    from monfile.actions.copy import CopyAction
    from monfile.actions.move import MoveAction

    return {cls.type_name: cls for cls in (CopyAction, MoveAction)}


def get_action(type_name: str) -> type[Action]:
    try:
        return _registry()[type_name]
    except KeyError:
        raise KeyError(f"unknown action type: {type_name!r}") from None
