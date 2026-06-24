from datetime import timedelta
from typing import Any

from openminion.modules.task import TaskLifecycleState

from .contracts import CronResumePolicy, CronSchedule

_BACKOFF_SECONDS = (30, 60, 120, 300, 600, 1800, 3600)


def _normalized_task_state(task_state: Any) -> str:
    raw = getattr(task_state, "state", task_state)
    return str(getattr(raw, "value", raw) or "").strip().lower()


class ExponentialBackoffResumePolicy(CronResumePolicy):
    def __init__(
        self,
        *,
        max_attempts: int = 50,
        max_elapsed: timedelta = timedelta(hours=24),
        backoff_seconds: tuple[int, ...] = _BACKOFF_SECONDS,
    ) -> None:
        self._max_attempts = max(1, int(max_attempts))
        self._max_elapsed = max(timedelta(seconds=1), max_elapsed)
        self._backoff_seconds = tuple(
            max(1, int(value)) for value in (backoff_seconds or _BACKOFF_SECONDS)
        )

    def should_create_cron_job(self, task_state: Any, mode_spec: Any) -> bool:
        return _normalized_task_state(
            task_state
        ) == TaskLifecycleState.PAUSED.value and bool(
            getattr(mode_spec, "has_resume", False)
        )

    def initial_schedule(self, task_state: Any, mode_spec: Any) -> CronSchedule:
        del task_state, mode_spec
        return CronSchedule.interval_schedule(
            timedelta(seconds=int(self._backoff_seconds[0]))
        )

    def should_stop_retrying(
        self,
        attempt_count: int,
        elapsed_time: timedelta,
        task_state: Any,
    ) -> bool:
        del task_state
        return (
            int(attempt_count) >= self._max_attempts
            or elapsed_time >= self._max_elapsed
        )

    def next_backoff_interval(
        self,
        attempt_count: int,
        current_interval: timedelta,
    ) -> timedelta:
        del current_interval
        index = max(0, min(int(attempt_count), len(self._backoff_seconds) - 1))
        return timedelta(seconds=int(self._backoff_seconds[index]))


class RecurringSchedulePolicy(CronResumePolicy):
    def __init__(
        self,
        *,
        interval: timedelta | None = None,
        cron_expr: str | None = None,
        timezone_name: str = "UTC",
    ) -> None:
        if cron_expr:
            self._schedule = CronSchedule.recurring(
                cron_expr=str(cron_expr),
                timezone_name=timezone_name,
            )
        else:
            self._schedule = CronSchedule.interval_schedule(
                interval or timedelta(days=1)
            )

    def should_create_cron_job(self, task_state: Any, mode_spec: Any) -> bool:
        del task_state
        return bool(getattr(mode_spec, "has_resume", False))

    def initial_schedule(self, task_state: Any, mode_spec: Any) -> CronSchedule:
        del task_state, mode_spec
        return self._schedule

    def should_stop_retrying(
        self,
        attempt_count: int,
        elapsed_time: timedelta,
        task_state: Any,
    ) -> bool:
        del attempt_count, elapsed_time, task_state
        return False

    def next_backoff_interval(
        self,
        attempt_count: int,
        current_interval: timedelta,
    ) -> timedelta:
        del attempt_count
        return current_interval


__all__ = ["ExponentialBackoffResumePolicy", "RecurringSchedulePolicy"]
