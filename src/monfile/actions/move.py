"""move action: copy + verify + unlink source (default), or same-fs rename (DESIGN §6.3)."""

from __future__ import annotations

from monfile.actions import Action, ActionContext, ActionResult


class MoveAction(Action):
    """Params: as for copy, plus mode ("copy" default | "rename"). Must be last in the chain."""

    type_name = "move"
    consumes_source = True

    def run(self, ctx: ActionContext) -> ActionResult:
        raise NotImplementedError
