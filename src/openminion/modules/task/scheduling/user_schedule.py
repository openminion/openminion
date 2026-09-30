from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from openminion.base.time import utc_now
from openminion.modules.task.constants import DEFAULT_TASK_MIN_EVERY_MS
from openminion.modules.task.errors import TaskScheduleIntervalTooShortError

from .schedule import normalize_schedule, to_iso_utc


def normalize_user_schedule(
    schedule: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    candidate = dict(schedule)
    if str(candidate.get("kind") or "").strip() == "at":
        has_at = candidate.get("at") not in (None, "")
        has_after = candidate.get("after_seconds") is not None
        if has_at == has_after:
            raise ValueError("at schedule requires exactly one of at or after_seconds")
        if has_after:
            delay = candidate["after_seconds"]
            if isinstance(delay, bool) or not isinstance(delay, int):
                raise ValueError("after_seconds must be a positive integer")
            if delay <= 0:
                raise ValueError("after_seconds must be greater than 0")
            candidate = {
                "kind": "at",
                "at": to_iso_utc((now or utc_now()) + timedelta(seconds=delay)),
            }

    normalized = normalize_schedule(candidate)
    if normalized["kind"] == "every":
        every_ms = int(normalized["every_ms"])
        if every_ms < DEFAULT_TASK_MIN_EVERY_MS:
            raise TaskScheduleIntervalTooShortError(
                every_ms=every_ms,
                minimum_every_ms=DEFAULT_TASK_MIN_EVERY_MS,
            )
    return normalized


__all__ = ["normalize_user_schedule"]
