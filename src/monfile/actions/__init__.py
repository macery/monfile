"""Action plugin interface and registry (DESIGN §6.1, ADR-0006).

To add an action: subclass Action, set type_name, and add it to REGISTRY.
Actions must be idempotent and honor ctx.dry_run.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from monfile.config import FolderConfig


@dataclass(frozen=True)
class ActionContext:
    folder: FolderConfig
    source: Path
    rel_path: str
    source_sha256: str | None
    dry_run: bool
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

    def __init__(self, params: Mapping[str, Any]) -> None:
        """Validate params at config load time. Raise ConfigError on bad input."""
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
