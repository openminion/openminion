from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from openminion.base.config.core import resolve_default_agent_id
from openminion.services.runtime.interfaces import RuntimeFacade
from openminion.modules.task.run import (
    RUN_STATE_COMPLETED,
    RUN_STATE_FAILED,
    RUN_STATE_QUEUED,
    RUN_STATE_RESPONDING,
    RUN_STATE_RUNNING,
    RUN_STATE_WAITING_TOOL,
    list_session_runs,
)

_ACTIVE_RUN_STATES = frozenset(
    {
        RUN_STATE_QUEUED,
        RUN_STATE_RUNNING,
        RUN_STATE_WAITING_TOOL,
        RUN_STATE_RESPONDING,
    }
)


def build_owner_status(
    config_path: Optional[str],
    *,
    runtime: RuntimeFacade,
    session_limit: int = 20,
    run_limit_per_session: int = 20,
    window_hours: int = 24,
) -> dict[str, Any]:
    safe_session_limit = _clamp_int(session_limit, minimum=1, maximum=500)
    safe_run_limit = _clamp_int(run_limit_per_session, minimum=1, maximum=500)
    safe_window_hours = _clamp_int(window_hours, minimum=1, maximum=24 * 14)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=safe_window_hours)
    provider_name, default_channel = _default_owner_bindings(runtime)
    sessions = runtime.sessions.list_sessions(
        limit=safe_session_limit,
        newest_first=True,
    )
    run_summary = _summarize_owner_runs(
        runtime,
        sessions=sessions,
        safe_run_limit=safe_run_limit,
        window_start=window_start,
    )
    return _owner_status_payload(
        now=now,
        window_start=window_start,
        safe_window_hours=safe_window_hours,
        safe_session_limit=safe_session_limit,
        safe_run_limit=safe_run_limit,
        sessions_total=runtime.sessions.count_sessions(),
        sessions=sessions,
        provider_name=provider_name,
        default_channel=default_channel,
        **run_summary,
    )


def _default_owner_bindings(active_runtime: RuntimeFacade) -> tuple[str, str]:
    default_agent_id = resolve_default_agent_id(active_runtime.config)
    default_profile = active_runtime.config.agents[default_agent_id]
    provider_name = (default_profile.provider or "echo").strip().lower() or "echo"
    default_channel = str(default_profile.default_channel or "").strip() or "console"
    return provider_name, default_channel


def _summarize_owner_runs(
    active_runtime: RuntimeFacade,
    *,
    sessions,
    safe_run_limit: int,
    window_start: datetime,
) -> dict[str, Any]:
    state_counts: dict[str, int] = _initial_run_state_counts()
    sessions_with_recent_runs: set[str] = set()
    recent_failures: list[dict[str, str]] = []
    session_summaries: list[dict[str, Any]] = []
    runs_total = 0
    latest_activity_at: Optional[datetime] = None
    for session in sessions:
        summary = _summarize_session_runs(
            active_runtime,
            session=session,
            safe_run_limit=safe_run_limit,
            window_start=window_start,
        )
        runs_total += summary["recent_run_count"]
        if summary["recent_run_count"]:
            sessions_with_recent_runs.add(session.id)
        _merge_state_counts(state_counts, summary["state_counts"])
        recent_failures.extend(summary["recent_failures"])
        latest_activity_at = _latest_timestamp(
            latest_activity_at, summary["latest_activity_at"]
        )
        session_summaries.append(summary["session_summary"])
    recent_failures.sort(key=lambda item: item.get("ended_at", ""), reverse=True)
    return {
        "state_counts": state_counts,
        "sessions_with_recent_runs": sessions_with_recent_runs,
        "recent_failures": recent_failures,
        "session_summaries": session_summaries,
        "runs_total": runs_total,
        "latest_activity_at": latest_activity_at,
    }


def _summarize_session_runs(
    active_runtime: RuntimeFacade,
    *,
    session,
    safe_run_limit: int,
    window_start: datetime,
) -> dict[str, Any]:
    runs = list_session_runs(
        active_runtime.sessions, session_id=session.id, limit=safe_run_limit
    )
    recent_runs = _filter_runs_within_window(runs=runs, window_start=window_start)
    state_counts: dict[str, int] = {}
    recent_failures: list[dict[str, str]] = []
    active_runs = 0
    failed_runs = 0
    latest_activity_at: Optional[datetime] = None
    for run in recent_runs:
        state = str(run.state or "").strip() or "unknown"
        state_counts[state] = state_counts.get(state, 0) + 1
        active_runs += int(state in _ACTIVE_RUN_STATES)
        if state == RUN_STATE_FAILED:
            failed_runs += 1
            recent_failures.append(_failure_payload(run))
        latest_activity_at = _latest_timestamp(
            latest_activity_at, _run_activity_at(run)
        )
    latest_run = recent_runs[0] if recent_runs else (runs[0] if runs else None)
    return {
        "recent_run_count": len(recent_runs),
        "state_counts": state_counts,
        "recent_failures": recent_failures,
        "latest_activity_at": latest_activity_at,
        "session_summary": _session_summary_payload(
            session, runs, recent_runs, active_runs, failed_runs, latest_run
        ),
    }


def _owner_status_payload(
    *,
    now: datetime,
    window_start: datetime,
    safe_window_hours: int,
    safe_session_limit: int,
    safe_run_limit: int,
    sessions_total: int,
    sessions,
    provider_name: str,
    default_channel: str,
    state_counts: dict[str, int],
    sessions_with_recent_runs: set[str],
    recent_failures: list[dict[str, str]],
    session_summaries: list[dict[str, Any]],
    runs_total: int,
    latest_activity_at: Optional[datetime],
) -> dict[str, Any]:
    failed_runs = int(state_counts.get(RUN_STATE_FAILED, 0))
    completed_runs = int(state_counts.get(RUN_STATE_COMPLETED, 0))
    active_runs = sum(int(state_counts.get(state, 0)) for state in _ACTIVE_RUN_STATES)
    success_denominator = completed_runs + failed_runs
    alerts = _owner_alerts(failed_runs, active_runs, runs_total, safe_window_hours)
    return {
        "generated_at": now.isoformat(),
        "window_hours": safe_window_hours,
        "window_start": window_start.isoformat(),
        "session_limit": safe_session_limit,
        "run_limit_per_session": safe_run_limit,
        "sessions_total": sessions_total,
        "sessions_considered": len(sessions),
        "summary": _summary_payload(
            runs_total,
            active_runs,
            failed_runs,
            completed_runs,
            state_counts,
            latest_activity_at,
            sessions_with_recent_runs,
            success_denominator,
        ),
        "heartbeat": _heartbeat_payload(
            now, alerts, failed_runs, active_runs, runs_total
        ),
        "daily_digest": _daily_digest_payload(
            window_start,
            now,
            runs_total,
            failed_runs,
            completed_runs,
            active_runs,
            success_denominator,
        ),
        "component_vocabulary": _component_vocabulary(provider_name, default_channel),
        "sessions": session_summaries,
        "recent_failures": recent_failures[:10],
        "alerts": alerts,
    }


def _filter_runs_within_window(*, runs, window_start: datetime):
    filtered = []
    for run in runs:
        run_started = _parse_timestamp(run.started_at)
        if run_started is not None and run_started < window_start:
            continue
        filtered.append(run)
    return filtered


def _initial_run_state_counts() -> dict[str, int]:
    return {
        RUN_STATE_QUEUED: 0,
        RUN_STATE_RUNNING: 0,
        RUN_STATE_WAITING_TOOL: 0,
        RUN_STATE_RESPONDING: 0,
        RUN_STATE_COMPLETED: 0,
        RUN_STATE_FAILED: 0,
    }


def _merge_state_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for state, count in source.items():
        target[state] = target.get(state, 0) + int(count)


def _run_activity_at(run) -> Optional[datetime]:
    return _parse_timestamp(run.ended_at) or _parse_timestamp(run.started_at)


def _latest_timestamp(
    current: Optional[datetime],
    candidate: Optional[datetime],
) -> Optional[datetime]:
    if candidate is None:
        return current
    if current is None or candidate > current:
        return candidate
    return current


def _failure_payload(run) -> dict[str, str]:
    return {
        "session_id": run.session_id,
        "run_id": run.run_id,
        "error": run.error or "",
        "ended_at": run.ended_at or run.started_at,
    }


def _session_summary_payload(
    session,
    runs,
    recent_runs,
    active_runs: int,
    failed_runs: int,
    latest_run,
) -> dict[str, Any]:
    return {
        "id": session.id,
        "channel": session.channel,
        "target": session.target,
        "updated_at": session.updated_at,
        "run_count": len(runs),
        "recent_run_count": len(recent_runs),
        "active_runs": active_runs,
        "failed_runs": failed_runs,
        "latest_run_id": latest_run.run_id if latest_run is not None else "",
        "latest_run_state": latest_run.state if latest_run is not None else "",
        "latest_run_at": (
            (latest_run.ended_at or latest_run.started_at)
            if latest_run is not None
            else ""
        ),
    }


def _owner_alerts(
    failed_runs: int,
    active_runs: int,
    runs_total: int,
    safe_window_hours: int,
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    if failed_runs > 0:
        alerts.append(
            {
                "level": "warn",
                "code": "recent_failures",
                "message": f"{failed_runs} failed run(s) in the last {safe_window_hours} hour(s).",
            }
        )
    if active_runs > 0:
        alerts.append(
            {
                "level": "info",
                "code": "active_runs",
                "message": f"{active_runs} run(s) currently in non-terminal states.",
            }
        )
    if runs_total == 0:
        alerts.append(
            {
                "level": "info",
                "code": "idle_window",
                "message": f"No runs observed in the last {safe_window_hours} hour(s).",
            }
        )
    return alerts


def _summary_payload(
    runs_total: int,
    active_runs: int,
    failed_runs: int,
    completed_runs: int,
    state_counts: dict[str, int],
    latest_activity_at: Optional[datetime],
    sessions_with_recent_runs: set[str],
    success_denominator: int,
) -> dict[str, Any]:
    return {
        "runs_total": runs_total,
        "active_runs": active_runs,
        "failed_runs": failed_runs,
        "completed_runs": completed_runs,
        "state_counts": state_counts,
        "success_rate": (
            round(completed_runs / success_denominator, 4)
            if success_denominator > 0
            else None
        ),
        "latest_activity_at": (
            latest_activity_at.isoformat() if latest_activity_at is not None else ""
        ),
        "sessions_with_recent_runs": len(sessions_with_recent_runs),
    }


def _heartbeat_payload(
    now: datetime,
    alerts: list[dict[str, str]],
    failed_runs: int,
    active_runs: int,
    runs_total: int,
) -> dict[str, Any]:
    status = "warn" if failed_runs > 0 else "idle" if runs_total == 0 else "ok"
    if status == "ok" and active_runs > 0:
        status = "active"
    return {
        "status": status,
        "generated_at": now.isoformat(),
        "alerts_count": len(alerts),
    }


def _daily_digest_payload(
    window_start: datetime,
    now: datetime,
    runs_total: int,
    failed_runs: int,
    completed_runs: int,
    active_runs: int,
    success_denominator: int,
) -> dict[str, Any]:
    return {
        "window_start": window_start.isoformat(),
        "window_end": now.isoformat(),
        "runs_total": runs_total,
        "failed_runs": failed_runs,
        "completed_runs": completed_runs,
        "active_runs": active_runs,
        "failure_ratio": (
            round(failed_runs / success_denominator, 4)
            if success_denominator > 0
            else None
        ),
    }


def _component_vocabulary(provider_name: str, default_channel: str) -> dict[str, Any]:
    return {
        "runtime": {
            "component_kind": "runtime_manager",
            "component_id": "primary",
            "scope": "system",
        },
        "provider": {
            "component_kind": "provider_binding",
            "component_id": provider_name,
            "scope": "system",
        },
        "channel": {
            "component_kind": "channel_adapter",
            "component_id": default_channel,
            "scope": "system",
        },
    }


def _parse_timestamp(raw_value: str) -> Optional[datetime]:
    candidate = str(raw_value or "").strip()
    if not candidate:
        return None
    normalized = candidate[:-1] + "+00:00" if candidate.endswith("Z") else candidate
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clamp_int(value: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))
