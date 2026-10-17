from __future__ import annotations

from dataclasses import dataclass
import logging
import sqlite3
from typing import Literal
from typing import Any
from uuid import uuid4

from openminion.cli.presentation.queue import (
    queue_cleared_notice,
    queue_command_usage_notice,
    queue_drop_missing_notice,
    queue_drop_notice,
    queue_drop_usage_notice,
    queue_listing,
)
from openminion.services.runtime.turn_input import (
    QUEUE_EVENT_CANCELLED,
    QUEUE_EVENT_COMPLETED,
    QUEUE_EVENT_DEQUEUED,
    QUEUE_EVENT_DROPPED,
    QUEUE_EVENT_ENQUEUED,
    QUEUE_EVENT_FAILED,
    QUEUE_EVENT_RUNNING,
    TurnInputQueue,
    TurnInputQueueEntry,
    TurnInputQueueStatus,
)

_LOGGER = logging.getLogger(__name__)
_TERMINAL_EVENTS = {
    TurnInputQueueStatus.COMPLETED: QUEUE_EVENT_COMPLETED,
    TurnInputQueueStatus.CANCELLED: QUEUE_EVENT_CANCELLED,
    TurnInputQueueStatus.FAILED: QUEUE_EVENT_FAILED,
}


@dataclass(frozen=True)
class QueueCommandResult:
    kind: Literal["message", "run_next"]
    message: str = ""
    reset_pause: bool = False


def record_queue_event(
    sessions: Any,
    event_type: str,
    entry: TurnInputQueueEntry,
) -> None:
    try:
        sessions.append_event(
            session_id=entry.session_id,
            event_type=event_type,
            payload=entry.event_payload(),
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        _LOGGER.warning(
            "Focus queue audit write failed for %s: %s",
            entry.queue_id,
            exc,
        )


def reserve_next_entry(
    *,
    queue: TurnInputQueue,
    sessions: Any,
    session_id: str,
    agent_id: str,
) -> TurnInputQueueEntry | None:
    reserved = queue.reserve_next(session_id=session_id, agent_id=agent_id)
    if reserved is None:
        return None
    record_queue_event(sessions, QUEUE_EVENT_DEQUEUED, reserved)
    running = queue.mark_running(queue_id=reserved.queue_id, trace_id=uuid4().hex)
    record_queue_event(sessions, QUEUE_EVENT_RUNNING, running)
    return running


def enqueue_entry(
    *,
    queue: TurnInputQueue,
    sessions: Any,
    session_id: str,
    agent_id: str,
    text: str,
) -> TurnInputQueueEntry:
    entry = queue.enqueue(
        session_id=session_id,
        agent_id=agent_id,
        text=text,
        source_client="focus",
    )
    record_queue_event(sessions, QUEUE_EVENT_ENQUEUED, entry)
    return entry


def terminalize_entry(
    *,
    queue: TurnInputQueue,
    sessions: Any,
    queue_id: str,
    status: TurnInputQueueStatus,
) -> None:
    entry = queue.mark_terminal(queue_id=queue_id, status=status)
    record_queue_event(sessions, _TERMINAL_EVENTS[status], entry)


def apply_queue_command(
    text: str,
    *,
    queue: TurnInputQueue,
    sessions: Any,
    session_id: str,
    agent_id: str,
) -> QueueCommandResult:
    parts = str(text or "").strip().split()
    entries = queue.list_entries(
        session_id=session_id,
        agent_id=agent_id,
        statuses={TurnInputQueueStatus.QUEUED},
    )
    if len(parts) == 1:
        return QueueCommandResult(
            "message", queue_listing([entry.text for entry in entries])
        )
    action = parts[1].lower()
    if action == "clear":
        dropped: list[TurnInputQueueEntry] = []
        for entry in entries:
            dropped_entry = queue.drop(
                session_id=session_id,
                queue_id=entry.queue_id,
                status_version=entry.status_version,
            )
            record_queue_event(sessions, QUEUE_EVENT_DROPPED, dropped_entry)
            dropped.append(dropped_entry)
        return QueueCommandResult(
            "message",
            queue_cleared_notice(len(dropped)),
            reset_pause=True,
        )
    if action == "drop":
        return _drop_queued_turn(parts, queue, sessions, session_id, entries)
    if action == "run-next":
        return QueueCommandResult("run_next")
    return QueueCommandResult("message", queue_command_usage_notice())


def _drop_queued_turn(
    parts: list[str],
    queue: TurnInputQueue,
    sessions: Any,
    session_id: str,
    entries: list[TurnInputQueueEntry],
) -> QueueCommandResult:
    if len(parts) < 3:
        return QueueCommandResult("message", queue_drop_usage_notice())
    try:
        index = int(parts[2])
    except ValueError:
        return QueueCommandResult("message", queue_drop_usage_notice())
    if index < 1 or index > len(entries):
        return QueueCommandResult("message", queue_drop_missing_notice(index))
    entry = entries[index - 1]
    dropped = queue.drop(
        session_id=session_id,
        queue_id=entry.queue_id,
        status_version=entry.status_version,
    )
    record_queue_event(sessions, QUEUE_EVENT_DROPPED, dropped)
    return QueueCommandResult(
        "message",
        queue_drop_notice(index, dropped.text),
    )


__all__ = [
    "QueueCommandResult",
    "apply_queue_command",
    "enqueue_entry",
    "reserve_next_entry",
    "terminalize_entry",
]
