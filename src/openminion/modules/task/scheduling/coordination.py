from __future__ import annotations

from typing import Any, Callable

from .interfaces import CronStoreProtocol

CronEventHook = Callable[[str, dict[str, Any]], None]
_SCHEDULER_STATUS_COMMAND = "openminion service status cron"


def scheduler_readiness_from_health(
    health_payload: dict[str, Any],
    *,
    reachable: bool,
    identity_matches: bool | None,
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
            scheduler = next(
                (
                    item
                    for item in snapshot.get("components", [])
                    if (item.get("component") or {}).get("component_kind")
                    == "cron_scheduler"
                ),
                None,
            )
            if scheduler is None:
                state, reason = "degraded", "scheduler_not_attached"
            else:
                readiness = str(scheduler.get("readiness") or "unknown")
                heartbeat = scheduler.get("last_heartbeat_at")
                if readiness == "ready" and heartbeat:
                    state, reason = "ready", None
                elif readiness == "ready":
                    reason = "scheduler_heartbeat_unavailable"
                else:
                    state = "degraded"
                    reason = scheduler.get("status_message")
    result = {"state": state, "hosted_by": "daemon", "reason": reason}
    if heartbeat:
        result["last_heartbeat_at"] = heartbeat
    if state != "ready":
        result["check_command"] = _SCHEDULER_STATUS_COMMAND
    return result


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
