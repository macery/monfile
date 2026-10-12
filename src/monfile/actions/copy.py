"""copy action: atomic, SHA-256-verified copy to a destination (DESIGN §6.3)."""

from __future__ import annotations

from monfile.actions import Action, ActionContext, ActionResult


class CopyAction(Action):
    """Params: destination (abs path), collision (suffix|skip|fail), preserve_tree (bool)."""

    type_name = "copy"

    def run(self, ctx: ActionContext) -> ActionResult:
        # Steps:
        # 1. open with O_NOFOLLOW and stream to .monfile-tmp-<rand> (0600), hashing as it goes
        # 2. compare the stream hash to ctx.source_sha256, then fsync
        # 3. re-read the temp file and verify its hash, then chmod
        # 4. resolve the name collision, os.replace, and fsync the dir
        raise NotImplementedError
