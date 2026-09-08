from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable, cast

from openminion.modules.task.constants import (
    DEFAULT_TASK_MIN_EVERY_MS,
    DEFAULT_TASK_NAME_MAX_CHARS,
    TASK_SCHEDULER_HEARTBEAT_STALE_AFTER_SECONDS,
)

from .interfaces import CronStoreProtocol
from .schedule import normalize_schedule

CronEventHook = Callable[[str, dict[str, Any]], None]
_SCHEDULER_STATUS_COMMAND = "openminion service status cron"


class ScheduleIntervalTooShortError(ValueError):
    def __init__(self, *, every_ms: int) -> None:
        self.every_ms = every_ms
        super().__init__(
            f"recurring interval must be at least {DEFAULT_TASK_MIN_EVERY_MS} ms"
        )


class ExpiredOneShotTaskError(ValueError):
    """Raised when a completed one-shot schedule cannot be resumed."""


def schedule_user_task(
    owner: Any,
    *,
    instruction: str,
    schedule: Mapping[str, Any],
    agent_id: str,
    name: str | None = None,
    origin: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_instruction = str(instruction or "").strip()
    if not normalized_instruction:
        raise ValueError("instruction is required")
    if not isinstance(schedule, Mapping):
        raise ValueError("schedule is required")

    normalized_schedule = normalize_schedule(schedule)
    if (
        normalized_schedule["kind"] == "every"
        and int(normalized_schedule["every_ms"]) < DEFAULT_TASK_MIN_EVERY_MS
    ):
        raise ScheduleIntervalTooShortError(
            every_ms=int(normalized_schedule["every_ms"])
        )

    normalized_name = " ".join(str(name or normalized_instruction).split())
    if len(normalized_name) > DEFAULT_TASK_NAME_MAX_CHARS:
        normalized_name = (
            f"{normalized_name[: DEFAULT_TASK_NAME_MAX_CHARS - 3].rstrip()}..."
        )
    normalized_origin = {
        str(key): str(value).strip()
        for key, value in (origin or {}).items()
        if str(key).strip() and str(value).strip()
    }
    create = getattr(owner, "schedule_user_task", None)
    if not callable(create):
        raise RuntimeError("task scheduling is unavailable")
    return cast(
        dict[str, Any],
        create(
            name=normalized_name,
            instruction=normalized_instruction,
            schedule=normalized_schedule,
            agent_id=str(agent_id).strip(),
            origin=normalized_origin,
        ),
    )


def scheduler_readiness_from_health(
    health_payload: dict[str, Any],
    *,
    reachable: bool,
    identity_matches: bool | None,
    now: datetime | None = None,
    stale_after_seconds: float = TASK_SCHEDULER_HEARTBEAT_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    state = "unknown"
    reason: str | None = "daemon_identity_unavailable"
    heartbeat: Any = None
    if not reachable:
        state, reason = "unreachable", "daemon_unreachable"
    elif identity_matches is False:
        state, reason = "degraded", "daemon_identity_mismatch"
    elif identity_matches:
        snapshot = health_payload.get("normalized_health_snapshot")
        if not isinstance(snapshot, dict) or "components" not in snapshot:
            reason = "health_snapshot_unavailable"
        else:
            components = snapshot.get("components")
            if not isinstance(components, list):
                components = None
            scheduler = None
            malformed_component = components is None
            for item in components or []:
                if not isinstance(item, Mapping):
                    malformed_component = True
                    continue
                component = item.get("component")
                if not isinstance(component, Mapping):
                    malformed_component = True
                    continue
                if component.get("component_kind") == "cron_scheduler":
                    scheduler = item
                    break
            if scheduler is None:
                if malformed_component:
                    reason = "health_components_invalid"
                else:
                    state, reason = "degraded", "scheduler_not_attached"
            else:
                readiness = str(scheduler.get("readiness") or "unknown")
                heartbeat = scheduler.get("last_heartbeat_at")
                if readiness == "ready" and heartbeat:
                    heartbeat_at = _parse_heartbeat(heartbeat)
                    if heartbeat_at is None:
                        reason = "scheduler_heartbeat_invalid"
                    else:
                        observed_at = now or datetime.now(timezone.utc)
                        if observed_at.tzinfo is None:
                            observed_at = observed_at.replace(tzinfo=timezone.utc)
                        age_seconds = (
                            observed_at.astimezone(timezone.utc) - heartbeat_at
                        ).total_seconds()
                        if age_seconds < 0:
                            state, reason = (
                                "degraded",
                                "scheduler_heartbeat_in_future",
                            )
                        elif age_seconds > max(0.0, stale_after_seconds):
                            state, reason = "degraded", "scheduler_heartbeat_stale"
                        else:
                            state, reason = "ready", None
                elif readiness == "ready":
                    reason = "scheduler_heartbeat_unavailable"
                else:
                    state = "degraded"
                    reason = scheduler.get("status_message") or "scheduler_not_ready"
    result = {"state": state, "hosted_by": "daemon", "reason": reason}
    if heartbeat:
        result["last_heartbeat_at"] = heartbeat
    if state != "ready":
        result["check_command"] = _SCHEDULER_STATUS_COMMAND
    return result


def _parse_heartbeat(value: Any) -> datetime | None:
    try:
        heartbeat = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if heartbeat.tzinfo is None:
        return None
    return heartbeat.astimezone(timezone.utc)


def recover_and_acquire_cron_runs(
    *,
    store: CronStoreProtocol,
    daemon_id: str,
    lease_ttl_seconds: int,
    capacity: int,
    can_start_background_work: Callable[[], bool],
    emit: CronEventHook,
) -> list[dict[str, Any]]:
    recovered = store.recover_expired_cron_runs()
    for item in recovered:
        event_type = (
            "cron.run.retry_exhausted"
            if item.get("state") == "failed"
            else "cron.run.lease_recovered"
        )
        emit(event_type, dict(item))
    store.enqueue_due_cron_runs(
        daemon_id,
        lease_ttl_s=lease_ttl_seconds,
        max_jobs=max(1, capacity * 2),
    )
    if not can_start_background_work():
        emit("cron.scheduler.foreground_deferred", {"capacity": capacity})
        return []
    return store.acquire_cron_runs(
        daemon_id,
        lease_ttl_s=lease_ttl_seconds,
        limit=capacity,
    )


def persist_cron_run_outcome(
    *,
    store: CronStoreProtocol,
    run_id: str,
    job_id: str,
    state: str,
    summary: str,
    artifact_refs: list[dict[str, Any]],
    output: dict[str, Any],
    error: dict[str, Any] | None,
    isolated_session_id: str | None,
    emit: CronEventHook,
) -> str:
    if error is not None:
        retried = store.retry_cron_run(run_id, error=error)
        if retried is not None:
            persisted_state = str(retried.get("state") or state)
            emit(
                (
                    "cron.run.retry_scheduled"
                    if persisted_state == "queued"
                    else "cron.run.retry_exhausted"
                ),
                {
                    "run_id": run_id,
                    "job_id": job_id,
                    "attempts": retried.get("attempts"),
                    "available_at": retried.get("available_at"),
                    "error": retried.get("error"),
                },
            )
            return persisted_state
        store.finish_cron_run(
            run_id,
            state=state,
            error=error,
            isolated_session_id=isolated_session_id,
        )
        return state

    store.finish_cron_run(
        run_id,
        state=state,
        summary=summary or None,
        artifact_refs=artifact_refs,
        output=output,
        isolated_session_id=isolated_session_id,
    )
    return state
