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
    result: dict[str, Any] = {
        "scheduled": False,
        "job_id": "",
        "reason": "",
        "interval_seconds": 0,
    }

    agent = str(agent_id or "").strip()
    session = str(session_id or "").strip()
    plan = str(plan_id or "").strip()
    if not agent or not session or not plan:
        result["reason"] = "missing_ids"
        _emit_pae_event(
            session_api=session_api,
            session_id=session,
            agent_id=agent,
            event_type="pae.idle_tick.suppressed",
            payload={"reason": "missing_ids", "plan_id": plan},
            trace_id=trace_id,
        )
        return result

    config = _resolve_pae_config(runner)
    if not _pae_is_enabled(config):
        result["reason"] = "disabled"
        _emit_pae_event(
            session_api=session_api,
            session_id=session,
            agent_id=agent,
            event_type="pae.idle_tick.suppressed",
            payload={"reason": "disabled", "plan_id": plan},
            trace_id=trace_id,
        )
        return result

    if cron_store is None:
        result["reason"] = "missing_cron_store"
        return result

    interval = int(getattr(config, "interval_seconds", 0) or 0)
    result["interval_seconds"] = interval
    try:
        grace_seconds = max(
            0, int(getattr(config, "user_activity_grace_seconds", 0) or 0)
        )
    except (TypeError, ValueError):
        grace_seconds = 0
    job_id = idle_tick_job_id(agent_id=agent, session_id=session, plan_id=plan)
    result["job_id"] = job_id

    existing = _safe_get_cron_job(cron_store=cron_store, job_id=job_id)
    if existing is not None:
        result["reason"] = "already_scheduled"
        return result

    try:
        cron_store.add_cron_job(
            name=job_id,
            schedule={"kind": "every", "every_ms": interval * 1000},
            payload={
                "kind": "agentIdleTick",
                "session_id": session,
                "plan_id": plan,
                "user_activity_grace_seconds": grace_seconds,
            },
            description=(
                f"PAE idle tick for agent={agent} session={session} plan={plan}"
            ),
            agent_id=agent,
            session_target="agent_session",
            job_id=job_id,
            enabled=True,
        )
    except Exception as exc:  # noqa: BLE001 — scheduling is best-effort
        result["reason"] = "schedule_failed"
        result["error"] = str(exc)
        _emit_pae_event(
            session_api=session_api,
            session_id=session,
            agent_id=agent,
            event_type="pae.idle_tick.suppressed",
            payload={
                "reason": "schedule_failed",
                "plan_id": plan,
                "error": str(exc),
            },
            trace_id=trace_id,
        )
        return result

    result["scheduled"] = True
    result["reason"] = "scheduled"
    _emit_pae_event(
        session_api=session_api,
        session_id=session,
        agent_id=agent,
        event_type="pae.idle_tick.scheduled",
        payload={
            "plan_id": plan,
            "interval_seconds": interval,
            "job_id": job_id,
        },
        trace_id=trace_id,
    )
    return result


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
