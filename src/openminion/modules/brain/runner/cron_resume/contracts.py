from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class CronSchedule:
    kind: Literal["interval", "cron"]
    interval: timedelta | None = None
    cron_expr: str | None = None
    timezone_name: str | None = None

    @classmethod
    def interval_schedule(cls, interval: timedelta) -> "CronSchedule":
        normalized = (
            interval if interval >= timedelta(seconds=1) else timedelta(seconds=1)
        )
        return cls(kind="interval", interval=normalized)

    @classmethod
    def recurring(
        cls,
        *,
        cron_expr: str,
        timezone_name: str = "UTC",
    ) -> "CronSchedule":
        expr = str(cron_expr or "").strip()
        if not expr:
            raise ValueError("cron_expr is required")
        return cls(
            kind="cron", cron_expr=expr, timezone_name=str(timezone_name or "UTC")
        )

    def to_store_schedule(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = (
            now.astimezone(timezone.utc)
            if now is not None
            else datetime.now(timezone.utc)
        )
        if self.kind == "interval":
            if self.interval is None:
                raise ValueError("interval schedule requires interval")
            due_at = (current + self.interval).astimezone(timezone.utc).isoformat()
            return {"kind": "at", "at": due_at}
        if self.kind == "cron":
            if self.cron_expr is None:
                raise ValueError("cron schedule requires cron_expr")
            return {
                "kind": "cron",
                "expr": self.cron_expr,
                "tz": str(self.timezone_name or "UTC"),
            }
        raise ValueError(f"unsupported cron schedule kind: {self.kind}")


@runtime_checkable
class CronResumePolicy(Protocol):
    def should_create_cron_job(self, task_state: Any, mode_spec: Any) -> bool: ...

    def initial_schedule(self, task_state: Any, mode_spec: Any) -> CronSchedule: ...

    def should_stop_retrying(
        self,
        attempt_count: int,
        elapsed_time: timedelta,
        task_state: Any,
    ) -> bool: ...

    def next_backoff_interval(
        self,
        attempt_count: int,
        current_interval: timedelta,
    ) -> timedelta: ...


@runtime_checkable
class CronJobLinker(Protocol):
    def link(self, task_id: str, cron_job_id: str) -> None: ...

    def unlink_and_delete(self, task_id: str) -> None: ...

    def get_linked_task(self, cron_job_id: str) -> str | None: ...

    def get_linked_cron_job(self, task_id: str) -> str | None: ...


__all__ = ["CronJobLinker", "CronResumePolicy", "CronSchedule"]
