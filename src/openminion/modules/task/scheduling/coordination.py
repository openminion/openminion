from __future__ import annotations

from typing import Any, Callable

from openminion.modules.task.constants import TASK_DELIVERY_ERROR_MESSAGE_LIMIT

from .interfaces import CronStoreProtocol

CronEventHook = Callable[[str, dict[str, Any]], None]
CronDeliveryHandler = Callable[[str, str, dict[str, Any], dict[str, Any], Any], None]
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
    control = store.get_cron_scheduler_control()
    if bool(control.get("paused")):
        emit(
            "cron.scheduler.paused",
            {
                "reason": control.get("reason"),
                "actor": control.get("actor"),
                "updated_at": control.get("updated_at"),
            },
        )
        return []
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
    daemon_id: str,
    expected_attempts: int,
    emit: CronEventHook,
) -> str:
    if error is not None and error.get("retryable") is True:
        retried = store.retry_cron_run(
            run_id,
            error=error,
            daemon_id=daemon_id,
            expected_attempts=expected_attempts,
        )
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
        emit(
            "cron.run.fence_rejected",
            {
                "run_id": run_id,
                "job_id": job_id,
                "daemon_id": daemon_id,
                "attempts": expected_attempts,
            },
        )
        return "lease_lost"

    finished = store.finish_cron_run(
        run_id,
        state=state,
        summary=summary or None,
        artifact_refs=artifact_refs,
        error=error,
        output=output,
        isolated_session_id=isolated_session_id,
        daemon_id=daemon_id,
        expected_attempts=expected_attempts,
    )
    if finished is not None:
        return state
    emit(
        "cron.run.fence_rejected",
        {
            "run_id": run_id,
            "job_id": job_id,
            "daemon_id": daemon_id,
            "attempts": expected_attempts,
        },
    )
    return "lease_lost"


def deliver_cron_run(
    *,
    store: CronStoreProtocol,
    job: dict[str, Any],
    run: dict[str, Any],
    result: Any,
    delivery_handler: CronDeliveryHandler | None,
    daemon_id: str,
    expected_attempts: int,
    emit: CronEventHook,
    lease_ttl_seconds: int = 60,
) -> dict[str, Any]:
    payload = job.get("payload", {})
    delivery = job.get("delivery", {})
    delivery = delivery if isinstance(delivery, dict) else {}
    mode = str(delivery.get("mode", "none") or "none").strip() or "none"
    if (
        (
            isinstance(payload, dict)
            and isinstance(payload.get("_openminion_watch"), dict)
            and not bool(result.output.get("watch_delivery_requested", False))
        )
        or mode == "none"
        or (mode == "webhook" and not result.summary.strip())
    ):
        return {"state": "not_requested", "mode": mode, "targets": []}

    to_value = str(delivery.get("to", "") or "").strip()
    marker = f"{mode}:{to_value or str(delivery.get('channel', '') or '').strip()}"
    try:
        if delivery_handler is None:
            raise RuntimeError(
                f"delivery mode '{mode}' is configured but no delivery handler is installed"
            )
        if not to_value and mode in {"announce", "webhook"}:
            raise RuntimeError("delivery target is required")
        lease_is_current = store.renew_cron_run_lease(
            str(run.get("run_id", "")),
            daemon_id=daemon_id,
            expected_attempts=expected_attempts,
            lease_ttl_s=lease_ttl_seconds,
        )
        if not lease_is_current:
            emit(
                "cron.delivery.fence_rejected",
                {"run_id": run.get("run_id"), "target": marker},
            )
            return {"state": "not_requested", "mode": mode, "targets": []}
        marked = store.mark_cron_delivery_target(
            str(run.get("run_id", "")),
            target=marker,
            daemon_id=daemon_id,
            expected_attempts=expected_attempts,
        )
        if not marked:
            emit(
                "cron.delivery.ambiguous",
                {"run_id": run.get("run_id"), "target": marker},
            )
            return {"state": "ambiguous", "mode": mode, "targets": [marker]}
        delivery_handler(mode, to_value, job, run, result)
        outcome = {"state": "succeeded", "mode": mode, "targets": [marker]}
        emit(
            "cron.delivery.succeeded",
            {
                "run_id": run.get("run_id"),
                "job_id": job.get("job_id"),
                "mode": mode,
                "target": marker,
            },
        )
        return outcome
    except Exception as exc:  # noqa: BLE001 - delivery must never retry execution
        error = {
            "code": "cron_delivery_failed",
            "message": str(exc)[:TASK_DELIVERY_ERROR_MESSAGE_LIMIT],
        }
        emit(
            "cron.delivery.failed",
            {
                "run_id": run.get("run_id"),
                "job_id": job.get("job_id"),
                "mode": mode,
                "target": marker,
                "best_effort": bool(delivery.get("best_effort", False)),
                "error": error,
            },
        )
        return {"state": "failed", "mode": mode, "targets": [], "error": error}
