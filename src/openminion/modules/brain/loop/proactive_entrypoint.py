import hashlib
from typing import Any

IDLE_TICK_JOB_NAME_PREFIX = "pae.idle_tick"


def idle_tick_job_id(*, agent_id: str, session_id: str, plan_id: str) -> str:
    """Derive the deterministic cron job id for an agent/session/plan triple."""
    h = hashlib.sha256(
        f"{agent_id}|{session_id}|{plan_id}".encode("utf-8")
    ).hexdigest()[:24]
    return f"{IDLE_TICK_JOB_NAME_PREFIX}:{h}"


def _resolve_pae_config(runner: Any) -> Any | None:
    """Resolve the PAE config from profile-first, runtime-fallback."""
    profile_cfg = getattr(
        getattr(runner, "profile", None),
        "proactive_autonomous_entrypoint",
        None,
    )
    if profile_cfg is not None:
        return profile_cfg
    return getattr(
        getattr(runner, "options", None),
        "proactive_autonomous_entrypoint_config",
        None,
    )


def _pae_is_enabled(config: Any) -> bool:
    if config is None:
        return False
    if not bool(getattr(config, "enabled", False)):
        return False
    try:
        interval = int(getattr(config, "interval_seconds", 0) or 0)
    except (TypeError, ValueError):
        return False
    return interval > 0


def _idle_tick_result(
    *,
    scheduled: bool = False,
    job_id: str = "",
    reason: str,
    interval_seconds: int = 0,
    error: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "scheduled": scheduled,
        "job_id": job_id,
        "reason": reason,
        "interval_seconds": interval_seconds,
    }
    if error:
        result["error"] = error
    return result


def _emit_idle_tick_suppressed(
    *,
    session_api: Any,
    session_id: str,
    agent_id: str,
    plan_id: str,
    reason: str,
    trace_id: str | None,
    error: str | None = None,
) -> None:
    payload = {"reason": reason, "plan_id": plan_id}
    if error:
        payload["error"] = error
    _emit_pae_event(
        session_api=session_api,
        session_id=session_id,
        agent_id=agent_id,
        event_type="pae.idle_tick.suppressed",
        payload=payload,
        trace_id=trace_id,
    )


def _suppressed_idle_tick_result(
    *,
    session_api: Any,
    session_id: str,
    agent_id: str,
    plan_id: str,
    reason: str,
    trace_id: str | None,
    error: str | None = None,
) -> dict[str, Any]:
    _emit_idle_tick_suppressed(
        session_api=session_api,
        session_id=session_id,
        agent_id=agent_id,
        plan_id=plan_id,
        reason=reason,
        trace_id=trace_id,
        error=error,
    )
    return _idle_tick_result(reason=reason, error=error)


def _user_activity_grace_seconds(config: Any) -> int:
    try:
        return max(0, int(getattr(config, "user_activity_grace_seconds", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _idle_tick_payload(*, session_id: str, plan_id: str, grace_seconds: int) -> dict[str, Any]:
    return {
        "kind": "agentIdleTick",
        "session_id": session_id,
        "plan_id": plan_id,
        "user_activity_grace_seconds": grace_seconds,
    }


def _add_idle_tick_job(
    *,
    cron_store: Any,
    job_id: str,
    interval_seconds: int,
    agent_id: str,
    session_id: str,
    plan_id: str,
    grace_seconds: int,
) -> None:
    cron_store.add_cron_job(
        name=job_id,
        schedule={"kind": "every", "every_ms": interval_seconds * 1000},
        payload=_idle_tick_payload(
            session_id=session_id,
            plan_id=plan_id,
            grace_seconds=grace_seconds,
        ),
        description=f"PAE idle tick for agent={agent_id} session={session_id} plan={plan_id}",
        agent_id=agent_id,
        session_target="agent_session",
        job_id=job_id,
        enabled=True,
    )


def _scheduled_idle_tick_result(
    *,
    session_api: Any,
    session_id: str,
    agent_id: str,
    plan_id: str,
    job_id: str,
    interval_seconds: int,
    trace_id: str | None,
) -> dict[str, Any]:
    _emit_pae_event(
        session_api=session_api,
        session_id=session_id,
        agent_id=agent_id,
        event_type="pae.idle_tick.scheduled",
        payload={
            "plan_id": plan_id,
            "interval_seconds": interval_seconds,
            "job_id": job_id,
        },
        trace_id=trace_id,
    )
    return _idle_tick_result(
        scheduled=True,
        job_id=job_id,
        reason="scheduled",
        interval_seconds=interval_seconds,
    )


def maybe_schedule_idle_tick(
    *,
    cron_store: Any,
    session_api: Any,
    runner: Any,
    session_id: str,
    agent_id: str,
    plan_id: str,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Schedule a session-scoped idle-tick cron job."""
    agent = str(agent_id or "").strip()
    session = str(session_id or "").strip()
    plan = str(plan_id or "").strip()
    if not agent or not session or not plan:
        return _suppressed_idle_tick_result(
            session_api=session_api,
            session_id=session,
            agent_id=agent,
            plan_id=plan,
            reason="missing_ids",
            trace_id=trace_id,
        )

    config = _resolve_pae_config(runner)
    if not _pae_is_enabled(config):
        return _suppressed_idle_tick_result(
            session_api=session_api,
            session_id=session,
            agent_id=agent,
            plan_id=plan,
            reason="disabled",
            trace_id=trace_id,
        )

    if cron_store is None:
        return _idle_tick_result(reason="missing_cron_store")

    interval = int(getattr(config, "interval_seconds", 0) or 0)
    grace_seconds = _user_activity_grace_seconds(config)
    job_id = idle_tick_job_id(agent_id=agent, session_id=session, plan_id=plan)

    existing = _safe_get_cron_job(cron_store=cron_store, job_id=job_id)
    if existing is not None:
        return _idle_tick_result(
            job_id=job_id,
            reason="already_scheduled",
            interval_seconds=interval,
        )

    try:
        _add_idle_tick_job(
            cron_store=cron_store,
            job_id=job_id,
            interval_seconds=interval,
            agent_id=agent,
            session_id=session,
            plan_id=plan,
            grace_seconds=grace_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — scheduling is best-effort
        return _suppressed_idle_tick_result(
            session_api=session_api,
            session_id=session,
            agent_id=agent,
            plan_id=plan,
            reason="schedule_failed",
            trace_id=trace_id,
            error=str(exc),
        )

    return _scheduled_idle_tick_result(
        session_api=session_api,
        session_id=session,
        agent_id=agent,
        plan_id=plan,
        job_id=job_id,
        interval_seconds=interval,
        trace_id=trace_id,
    )


def cancel_idle_tick(
    *,
    cron_store: Any,
    session_api: Any,
    session_id: str,
    agent_id: str,
    plan_id: str,
    reason: str = "plan_terminal",
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Cancel the session-scoped idle-tick cron job."""
    result: dict[str, Any] = {
        "cancelled": False,
        "job_id": "",
        "reason": reason,
    }
    agent = str(agent_id or "").strip()
    session = str(session_id or "").strip()
    plan = str(plan_id or "").strip()
    if not agent or not session or not plan or cron_store is None:
        return result

    job_id = idle_tick_job_id(agent_id=agent, session_id=session, plan_id=plan)
    result["job_id"] = job_id

    existing = _safe_get_cron_job(cron_store=cron_store, job_id=job_id)
    if existing is None:
        return result

    try:
        cron_store.delete_cron_job(job_id)
    except Exception:  # noqa: BLE001 — cancellation is best-effort
        return result

    result["cancelled"] = True
    _emit_pae_event(
        session_api=session_api,
        session_id=session,
        agent_id=agent,
        event_type="pae.idle_tick.cancelled",
        payload={
            "plan_id": plan,
            "job_id": job_id,
            "reason": reason,
        },
        trace_id=trace_id,
    )
    return result


def last_user_message_timestamp(*, session_api: Any, session_id: str) -> str | None:
    """Return the ISO timestamp of the most recent user turn, or None."""
    if not session_api or not session_id:
        return None
    lister = getattr(session_api, "list_events", None)
    if not callable(lister):
        return None
    try:
        events = lister(session_id)
    except Exception:  # noqa: BLE001 — best-effort lookup
        return None
    if not isinstance(events, list):
        return None
    latest: str | None = None
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        if event_type != "turn.user":
            continue
        ts = str(event.get("timestamp") or "").strip()
        if not ts:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


def is_user_active(
    *,
    session_api: Any,
    session_id: str,
    grace_seconds: int,
    now_iso: str | None = None,
) -> bool:
    """Return whether the session had a recent user turn inside the grace window."""
    if grace_seconds <= 0:
        return False
    last = last_user_message_timestamp(session_api=session_api, session_id=session_id)
    if last is None:
        return False
    from datetime import datetime, timezone, timedelta

    def _parse(iso: str) -> datetime | None:
        try:
            # Support both "...Z" and "...+00:00".
            if iso.endswith("Z"):
                iso = iso[:-1] + "+00:00"
            return datetime.fromisoformat(iso).astimezone(timezone.utc)
        except Exception:  # noqa: BLE001
            return None

    last_dt = _parse(last)
    if last_dt is None:
        return False
    if now_iso:
        now_dt = _parse(now_iso) or datetime.now(timezone.utc)
    else:
        now_dt = datetime.now(timezone.utc)
    return (now_dt - last_dt) <= timedelta(seconds=grace_seconds)


def _safe_get_cron_job(*, cron_store: Any, job_id: str) -> dict[str, Any] | None:
    getter = getattr(cron_store, "get_cron_job", None)
    if not callable(getter):
        return None
    try:
        result = getter(job_id)
    except Exception:  # noqa: BLE001
        return None
    return result if isinstance(result, dict) else None


def _emit_pae_event(
    *,
    session_api: Any,
    session_id: str,
    agent_id: str,
    event_type: str,
    payload: dict[str, Any],
    trace_id: str | None,
) -> None:
    if not session_api or not session_id:
        return
    append_event = getattr(session_api, "append_event", None)
    if not callable(append_event):
        return
    try:
        append_event(
            session_id,
            event_type,
            dict(payload),
            actor_type="system",
            actor_id=agent_id or None,
            trace={"trace_id": trace_id} if trace_id else None,
            importance=2,
            redaction="none",
            status="ok",
        )
    except Exception:  # noqa: BLE001 — telemetry is best-effort
        return
