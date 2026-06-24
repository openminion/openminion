"""Shared scheduled-task view builders for task tool responses."""

from collections.abc import Mapping
from typing import Any

from openminion.modules.task import TaskManager
from openminion.services.cron.scheduling import normalize_schedule

from ..constants import (
    FIRST_RUN_PENDING_NOTE,
    FIRST_RUN_PENDING_STATE,
)
from .runtime import (
    _consolidation_metadata_from_payload,
    _safe_str,
    _text,
    _watch_metadata_from_payload,
)


def _render_schedule_summary(schedule: Mapping[str, Any]) -> str:
    normalized = normalize_schedule(schedule)
    kind = _safe_str(normalized, "kind")
    if kind == "every":
        every_ms = int(normalized.get("every_ms", 0) or 0)
        return f"every:{every_ms}ms"
    if kind == "cron":
        expr = _safe_str(normalized, "expr")
        tz_name = _safe_str(normalized, "tz", "UTC")
        return f"cron:{expr} tz={tz_name}"
    return f"at:{normalized.get('at')}"


def _latest_run_fields(
    manager: TaskManager, *, job_id: str
) -> tuple[str | None, str | None, str | None]:
    runs = manager.list_scheduled_runs(job_id=job_id, limit=1)
    if not runs:
        return None, None, None
    latest = runs[0]
    state = _safe_str(latest, "state") or None
    timestamp = (
        latest.get("finished_at")
        or latest.get("started_at")
        or latest.get("due_at")
        or latest.get("created_at")
    )
    summary = _text(latest.get("summary")) or None
    return state, timestamp, summary


def _recent_runs(
    manager: TaskManager,
    *,
    job_id: str,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    effective_limit = max(1, min(int(limit), 20))
    runs = manager.list_scheduled_runs(job_id=job_id, limit=effective_limit)
    recent: list[dict[str, Any]] = []
    failure_count = 0
    for run in runs:
        state = _safe_str(run, "state")
        if state in {"failed", "timed_out"}:
            failure_count += 1
        recent.append(
            {
                "run_id": _safe_str(run, "run_id"),
                "state": state,
                "due_at": run.get("due_at"),
                "started_at": run.get("started_at"),
                "finished_at": run.get("finished_at"),
                "summary": run.get("summary"),
                "attempts": int(run.get("attempts", 0) or 0),
                "error": run.get("error"),
            }
        )
    return recent, failure_count


def _task_show_payload(
    *,
    manager: TaskManager,
    job: Mapping[str, Any],
    runs_limit: int,
    pause_reason: str | None = None,
) -> dict[str, Any]:
    task_id = _safe_str(job, "job_id")
    schedule_obj = dict(job.get("schedule") or {})
    try:
        schedule_summary = _render_schedule_summary(schedule_obj)
    except Exception:
        schedule_summary = "invalid-schedule"
    latest_run_state, latest_run_at, _ = _latest_run_fields(manager, job_id=task_id)
    runs, failure_count = _recent_runs(manager, job_id=task_id, limit=runs_limit)
    watch = _watch_metadata_from_payload(job.get("payload"))
    consolidation = _consolidation_metadata_from_payload(job.get("payload"))
    return {
        "task_id": task_id,
        "name": _safe_str(job, "name"),
        "enabled": bool(job.get("enabled", False)),
        "agent_id": _safe_str(job, "agent_id") or None,
        "schedule": schedule_obj,
        "schedule_summary": schedule_summary,
        "next_due_at": job.get("next_due_at"),
        "session_target": _safe_str(job, "session_target"),
        "delete_after_run": bool(job.get("delete_after_run", False)),
        "latest_run_state": latest_run_state,
        "latest_run_at": latest_run_at,
        "failure_count": failure_count,
        "pause_reason": pause_reason,
        "runs": runs,
        "watch": watch,
        "consolidation": consolidation,
    }


def _task_list_payload(
    *,
    manager: TaskManager,
    jobs: list[dict[str, Any]],
    effective_limit: int,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for job in jobs:
        schedule_obj = dict(job.get("schedule") or {})
        try:
            summary = _render_schedule_summary(schedule_obj)
        except Exception:
            summary = "invalid-schedule"

        last_run_state, last_run_at, last_run_summary = _latest_run_fields(
            manager,
            job_id=_safe_str(job, "job_id"),
        )
        pending_first_run = last_run_state is None and last_run_at is None
        watch = _watch_metadata_from_payload(job.get("payload"))
        consolidation = _consolidation_metadata_from_payload(job.get("payload"))
        tasks.append(
            {
                "task_id": _safe_str(job, "job_id"),
                "name": _safe_str(job, "name"),
                "enabled": bool(job.get("enabled", False)),
                "next_due_at": job.get("next_due_at"),
                "schedule": {
                    **schedule_obj,
                    "summary": summary,
                },
                "last_run_state": (
                    FIRST_RUN_PENDING_STATE if pending_first_run else last_run_state
                ),
                "last_run_at": last_run_at,
                "pending_first_run": pending_first_run,
                "last_run_note": (
                    FIRST_RUN_PENDING_NOTE if pending_first_run else None
                ),
                "watch": (
                    {
                        **watch,
                        "checks_completed": int(
                            (watch or {}).get("checks_completed", 0) or 0
                        ),
                        "last_check_result": last_run_summary
                        or (watch or {}).get("last_check_summary"),
                    }
                    if watch is not None
                    else None
                ),
                "consolidation": consolidation,
            }
        )
        if len(tasks) >= effective_limit:
            break
    return tasks


__all__ = [
    "_latest_run_fields",
    "_task_list_payload",
    "_task_show_payload",
]
