from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

from openminion.cli.presentation.queue import (
    queue_cleared_notice,
    queue_command_usage_notice,
    queue_drop_missing_notice,
    queue_drop_notice,
    queue_drop_usage_notice,
    queue_listing,
)


@dataclass(frozen=True)
class QueueCommandResult:
    kind: Literal["message", "run_next"]
    message: str = ""
    reset_pause: bool = False


def apply_queue_command(text: str, pending_turns: deque[str]) -> QueueCommandResult:
    parts = str(text or "").strip().split()
    if len(parts) == 1:
        return QueueCommandResult("message", queue_listing(list(pending_turns)))
    action = parts[1].lower()
    if action == "clear":
        count = len(pending_turns)
        pending_turns.clear()
        return QueueCommandResult(
            "message", queue_cleared_notice(count), reset_pause=True
        )
    if action == "drop":
        return _drop_queued_turn(parts, pending_turns)
    if action == "run-next":
        return QueueCommandResult("run_next")
    return QueueCommandResult("message", queue_command_usage_notice())


def _drop_queued_turn(
    parts: list[str],
    pending_turns: deque[str],
) -> QueueCommandResult:
    if len(parts) < 3:
        return QueueCommandResult("message", queue_drop_usage_notice())
    try:
        index = int(parts[2])
    except ValueError:
        return QueueCommandResult("message", queue_drop_usage_notice())
    if index < 1 or index > len(pending_turns):
        return QueueCommandResult("message", queue_drop_missing_notice(index))
    dropped = pending_turns[index - 1]
    del pending_turns[index - 1]
    return QueueCommandResult("message", queue_drop_notice(index, dropped))


__all__ = ["QueueCommandResult", "apply_queue_command"]
