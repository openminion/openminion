"""Telemetry stampers for session fork and file restore events."""

from typing import Any

from openminion.modules.session.fork_restore.fork import SessionForkRecord
from openminion.modules.session.fork_restore.restore import FileRestoreResult
from openminion.modules.telemetry.events.catalog import (
    SFRX_FILE_RESTORE,
    SFRX_SESSION_FORK,
)


def _stamp_event(logger: Any, *, event_type: str, payload: dict[str, Any]) -> None:
    if logger is None:
        return
    try:
        logger.log_canonical_event(event_type=event_type, payload=payload)
    except Exception:
        pass


def stamp_session_fork(logger: Any, record: SessionForkRecord) -> None:
    _stamp_event(
        logger,
        event_type=SFRX_SESSION_FORK,
        payload={
            "fork_id": record.fork_id,
            "parent_session_id": record.parent_session_id,
            "new_session_id": record.new_session_id,
            "snapshot_id": record.snapshot_id,
            "decision_action": record.decision_action,
            "name": record.name,
            "forked_at": record.forked_at,
        },
    )


def stamp_file_restore(logger: Any, result: FileRestoreResult) -> None:
    _stamp_event(
        logger,
        event_type=SFRX_FILE_RESTORE,
        payload={
            "checkpoint_id": result.checkpoint_id,
            "restored_paths": list(result.restored_paths),
            "missing_paths": list(result.missing_paths),
        },
    )


__all__ = ["stamp_file_restore", "stamp_session_fork"]
