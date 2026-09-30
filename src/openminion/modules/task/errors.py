from __future__ import annotations

from typing import Any

from .constants import (
    TASK_REASON_INVENTORY_UNAVAILABLE,
    TASK_REASON_RESUME_EXPIRED_ONE_SHOT,
    TASK_REASON_SCHEDULE_INTERVAL_TOO_SHORT,
)


class TaskResumeExpiredError(ValueError):
    code = TASK_REASON_RESUME_EXPIRED_ONE_SHOT

    def __init__(self, *, task_id: str) -> None:
        self.details: dict[str, Any] = {"task_id": task_id}
        super().__init__("One-shot task is already expired and cannot be resumed")


class TaskInventoryUnavailableError(RuntimeError):
    code = TASK_REASON_INVENTORY_UNAVAILABLE

    def __init__(self, message: str) -> None:
        self.details: dict[str, Any] = {}
        super().__init__(message)


class TaskScheduleIntervalTooShortError(ValueError):
    code = TASK_REASON_SCHEDULE_INTERVAL_TOO_SHORT

    def __init__(self, *, every_ms: int, minimum_every_ms: int) -> None:
        self.details = {
            "field": "schedule.every_ms",
            "every_ms": every_ms,
            "minimum_every_ms": minimum_every_ms,
        }
        super().__init__(f"recurring interval must be at least {minimum_every_ms} ms")


__all__ = [
    "TaskInventoryUnavailableError",
    "TaskResumeExpiredError",
    "TaskScheduleIntervalTooShortError",
]
