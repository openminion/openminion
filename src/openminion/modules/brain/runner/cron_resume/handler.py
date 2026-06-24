from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from openminion.modules.task import TaskLifecycleState

from .contracts import CronSchedule
from .linker import DefaultCronJobLinker, normalized_text

from openminion.base.time import utc_now as _utc_now


def _elapsed_since(iso_value: str) -> timedelta:
    raw = normalized_text(iso_value)
    if not raw:
        return timedelta(0)
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return timedelta(0)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(timedelta(0), _utc_now() - parsed.astimezone(timezone.utc))


@dataclass(frozen=True, slots=True)
class CronResumeSelection:
    task_id: str | None = None
    cron_job_id: str | None = None
    orphan_cleaned: bool = False


def resolve_cron_resume_selection(
    *,
    task_manager: Any,
    task_id_hint: str | None,
    cron_job_id_hint: str | None,
) -> CronResumeSelection:
    task_id = normalized_text(task_id_hint)
    cron_job_id = normalized_text(cron_job_id_hint)
    if not task_id:
        return CronResumeSelection()
    record = task_manager.get_task(task_id) if task_manager is not None else None
    if record is None:
        if cron_job_id:
            task_manager.delete_scheduled_job(cron_job_id)
            return CronResumeSelection(cron_job_id=cron_job_id, orphan_cleaned=True)
        return CronResumeSelection()
    state_name = normalized_text(
        getattr(getattr(record, "state", None), "value", getattr(record, "state", None))
    ).lower()
    if state_name in {
        TaskLifecycleState.CANCELLED.value,
        TaskLifecycleState.DONE.value,
        TaskLifecycleState.FAILED.value,
    }:
        DefaultCronJobLinker(task_manager=task_manager).unlink_and_delete(
            record.task_id
        )
        return CronResumeSelection(
            task_id=record.task_id,
            cron_job_id=cron_job_id or None,
            orphan_cleaned=True,
        )
    return CronResumeSelection(task_id=record.task_id, cron_job_id=cron_job_id or None)


def schedule_linked_resume_job(
    *,
    task_manager: Any,
    task_id: str,
    session_id: str,
    agent_id: str | None,
    schedule: CronSchedule,
    message: str,
    delivery: dict[str, Any] | None = None,
    recurring: bool = False,
    payload_extras: dict[str, Any] | None = None,
) -> str:
    normalized_task_id = normalized_text(task_id)
    if not normalized_task_id:
        raise ValueError("task_id is required")
    payload = {
        "kind": "agentTurn",
        "message": str(message or "").strip() or "Resume scheduled work.",
        "session_id": str(session_id or "").strip(),
        "linked_task_id": normalized_task_id,
    }
    if payload_extras:
        payload.update(dict(payload_extras))
    job_id = task_manager.create_cron_job(
        name=f"resume-{normalized_task_id[:12]}",
        schedule=schedule.to_store_schedule(now=_utc_now()),
        payload=payload,
        agent_id=agent_id,
        session_target="isolated",
        wake_mode="now",
        delivery=delivery,
        delete_after_run=not recurring,
    )
    DefaultCronJobLinker(task_manager=task_manager).link(normalized_task_id, job_id)
    return job_id


def schedule_backoff_resume(
    *,
    task_manager: Any,
    task_id: str,
    session_id: str,
    agent_id: str | None,
    goal: str,
    mode_name: str,
    interval: timedelta,
    attempt_count: int,
    first_scheduled_at: str,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")
    linker = DefaultCronJobLinker(task_manager=task_manager)
    prior_job_id = linker.get_linked_cron_job(task_id)
    if prior_job_id:
        task_manager.delete_scheduled_job(prior_job_id)
    job_id = schedule_linked_resume_job(
        task_manager=task_manager,
        task_id=task_id,
        session_id=session_id,
        agent_id=agent_id,
        schedule=CronSchedule.interval_schedule(interval),
        message=f"Resume {mode_name}: {goal}",
        payload_extras={"resume_kind": "backoff", "mode_name": mode_name},
    )
    metadata = dict(getattr(record, "metadata", {}) or {})
    metadata["linked_cron_job_id"] = job_id
    metadata["cron_resume_attempt_count"] = int(attempt_count)
    metadata["cron_resume_current_interval_s"] = int(max(1, interval.total_seconds()))
    metadata["cron_resume_first_scheduled_at"] = str(
        first_scheduled_at or _utc_now().isoformat()
    )
    if extra_metadata:
        metadata.update(dict(extra_metadata))
    task_manager.update_task_metadata(task_id=task_id, metadata=metadata)
    return job_id


def schedule_recurring_resume(
    *,
    task_manager: Any,
    task_id: str,
    session_id: str,
    agent_id: str | None,
    cron_expr: str,
    timezone_name: str,
    goal: str,
    mode_name: str,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")
    linker = DefaultCronJobLinker(task_manager=task_manager)
    prior_job_id = linker.get_linked_cron_job(task_id)
    if prior_job_id:
        task_manager.delete_scheduled_job(prior_job_id)
    job_id = schedule_linked_resume_job(
        task_manager=task_manager,
        task_id=task_id,
        session_id=session_id,
        agent_id=agent_id,
        schedule=CronSchedule.recurring(
            cron_expr=cron_expr,
            timezone_name=timezone_name,
        ),
        message=f"Refresh {mode_name}: {goal}",
        recurring=True,
        payload_extras={"resume_kind": "recurring", "mode_name": mode_name},
    )
    metadata = dict(getattr(record, "metadata", {}) or {})
    metadata["linked_cron_job_id"] = job_id
    if extra_metadata:
        metadata.update(dict(extra_metadata))
    task_manager.update_task_metadata(task_id=task_id, metadata=metadata)
    return job_id


def next_attempt_state(record: Any) -> tuple[int, timedelta, timedelta, str]:
    metadata = dict(getattr(record, "metadata", {}) or {})
    attempt_count = int(metadata.get("cron_resume_attempt_count", 0) or 0)
    current_interval = timedelta(
        seconds=max(1, int(metadata.get("cron_resume_current_interval_s", 30) or 30))
    )
    first_scheduled_at = (
        normalized_text(metadata.get("cron_resume_first_scheduled_at"))
        or _utc_now().isoformat()
    )
    return (
        attempt_count,
        current_interval,
        _elapsed_since(first_scheduled_at),
        first_scheduled_at,
    )


__all__ = [
    "CronResumeSelection",
    "next_attempt_state",
    "resolve_cron_resume_selection",
    "schedule_backoff_resume",
    "schedule_linked_resume_job",
    "schedule_recurring_resume",
]
