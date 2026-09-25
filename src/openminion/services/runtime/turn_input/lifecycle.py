"""Project terminal runtime events onto queued turn inputs."""

from __future__ import annotations

import sqlite3
from typing import Any

from openminion.base.logging import format_structured_event, get_logger

from .queue import (
    QUEUE_EVENT_CANCELLED,
    QUEUE_EVENT_COMPLETED,
    QUEUE_EVENT_FAILED,
    TurnInputQueueStatus,
)

_LOGGER = get_logger("turn_input")
_TERMINAL_EVENTS = {
    "runtime.turn.completed": (
        TurnInputQueueStatus.COMPLETED,
        QUEUE_EVENT_COMPLETED,
    ),
    "runtime.turn.cancelled": (
        TurnInputQueueStatus.CANCELLED,
        QUEUE_EVENT_CANCELLED,
    ),
    "runtime.turn.failed": (TurnInputQueueStatus.FAILED, QUEUE_EVENT_FAILED),
}


def project_terminal_runtime_event(
    *,
    queue: Any,
    sessions: Any,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    terminal = _TERMINAL_EVENTS.get(event_type)
    if terminal is None or payload.get("terminal") is not True:
        return
    entry = queue.mark_terminal_by_trace(
        trace_id=str(payload.get("trace_id", "")).strip(),
        status=terminal[0],
    )
    if entry is None:
        return
    try:
        sessions.append_event(
            session_id=entry.session_id,
            event_type=terminal[1],
            payload=entry.event_payload(),
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        _LOGGER.warning(
            format_structured_event(
                "turn_input.terminal_audit_failed",
                queue_id=entry.queue_id,
                trace_id=entry.trace_id,
                error=exc,
            )
        )


__all__ = ["project_terminal_runtime_event"]
