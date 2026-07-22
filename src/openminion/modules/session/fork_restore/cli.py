"""Dispatchers for `/session fork` and `/restore`."""

from typing import Any
from collections.abc import Sequence

from openminion.modules.session.fork_restore.fork import (
    SessionForkAPI,
)
from openminion.modules.session.fork_restore.restore import (
    FileCheckpoint,
    restore_file_checkpoint,
)


_SESSION_FORK_USAGE = "/session fork <parent_session_id> [name]"
_RESTORE_USAGE = "/restore <checkpoint_id>"


def dispatch_session_fork_command(
    api: SessionForkAPI, argv: Sequence[str]
) -> dict[str, Any]:
    if not argv:
        return {"ok": False, "error": "usage", "usage": _SESSION_FORK_USAGE}
    parent = argv[0]
    name = argv[1] if len(argv) > 1 else ""
    record = api.fork(parent, new_name=name)
    return {
        "ok": True,
        "fork": {
            "fork_id": record.fork_id,
            "parent_session_id": record.parent_session_id,
            "new_session_id": record.new_session_id,
            "snapshot_id": record.snapshot_id,
            "forked_at": record.forked_at,
            "name": record.name,
        },
    }


def dispatch_restore_command(
    checkpoint: FileCheckpoint | None,
    *,
    root: str = "",
) -> dict[str, Any]:
    if checkpoint is None:
        return {"ok": False, "error": "no_checkpoint", "usage": _RESTORE_USAGE}
    result = restore_file_checkpoint(checkpoint, root=root)
    return {
        "ok": True,
        "checkpoint_id": result.checkpoint_id,
        "restored_paths": list(result.restored_paths),
        "missing_paths": list(result.missing_paths),
    }


__all__ = [
    "dispatch_restore_command",
    "dispatch_session_fork_command",
]
