from typing import Any, TYPE_CHECKING

from openminion.modules.brain.config import (
    DEFAULT_MAX_AUTONOMOUS_TURNS_PER_PLAN,
    DEFAULT_MAX_AUTONOMOUS_TURNS_PER_SESSION,
)
from openminion.modules.brain.constants import (
    AUTONOMOUS_CONTINUATION_STOPPED_CAUSE_MAX_CHARS as _STOPPED_CAUSE_MAX_CHARS,
)
from openminion.base.constants import STATE_KEY_WORKING

if TYPE_CHECKING:  # pragma: no cover
    from openminion.modules.brain.runner.coordinator import BrainRunner

AUTONOMOUS_TURN_FIRED_EVENT = "autonomous_turn.fired"


class AutonomousContinuationCapsExceeded(Exception):
    """Raised when caller tries to schedule a continuation past the caps.

    Callers should catch this and terminate the continuation cycle
    cleanly — do not bypass.
    """

    def __init__(self, *, reason: str, details: dict[str, Any]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = dict(details)


def _read_session_events_strict(
    *, session_api: Any, session_id: str
) -> list[dict[str, Any]]:
    """Read the canonical event log or fail closed for cap enforcement."""
    if not session_api or not session_id:
        raise AutonomousContinuationCapsExceeded(
            reason="counter_unavailable",
            details={
                "cause": "missing_session_api_or_session_id",
                "session_id": str(session_id or ""),
            },
        )
    lister = getattr(session_api, "list_events", None)
    if not callable(lister):
        raise AutonomousContinuationCapsExceeded(
            reason="counter_unavailable",
            details={
                "cause": "session_api_missing_list_events",
                "session_id": str(session_id or ""),
            },
        )
    try:
        events = lister(session_id)
    except Exception as exc:  # noqa: BLE001
        raise AutonomousContinuationCapsExceeded(
            reason="counter_unavailable",
            details={
                "cause": str(exc),
                "session_id": str(session_id or ""),
            },
        ) from exc
    if not isinstance(events, list):
        raise AutonomousContinuationCapsExceeded(
            reason="counter_unavailable",
            details={
                "cause": "list_events_returned_non_list",
                "session_id": str(session_id or ""),
                "type": type(events).__name__,
            },
        )
    return events


def _count_autonomous_turns_from_events(
    events: list[dict[str, Any]],
    *,
    plan_id: str | None = None,
) -> int:
    target_plan = str(plan_id or "").strip() or None
    count = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type") or "").strip() != AUTONOMOUS_TURN_FIRED_EVENT:
            continue
        if target_plan is not None:
            payload = event.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if str(payload.get("plan_id") or "").strip() != target_plan:
                continue
        count += 1
    return count


def count_autonomous_turns(
    *,
    session_api: Any,
    session_id: str,
    plan_id: str | None = None,
) -> int:
    """Return an inspection-only count; safety-critical callers must not use it."""
    try:
        events = _read_session_events_strict(
            session_api=session_api, session_id=session_id
        )
    except AutonomousContinuationCapsExceeded:
        return 0
    return _count_autonomous_turns_from_events(events, plan_id=plan_id)


def check_autonomous_continuation_caps(
    *,
    session_api: Any,
    session_id: str,
    plan_id: str,
    max_per_plan: int = DEFAULT_MAX_AUTONOMOUS_TURNS_PER_PLAN,
    max_per_session: int = DEFAULT_MAX_AUTONOMOUS_TURNS_PER_SESSION,
) -> dict[str, Any]:
    """Summarize per-plan and per-session continuation caps without raising."""
    plan_id_str = str(plan_id or "").strip()
    per_plan_cap = max(0, int(max_per_plan or 0))
    per_session_cap = max(0, int(max_per_session or 0))

    try:
        events = _read_session_events_strict(
            session_api=session_api, session_id=session_id
        )
    except AutonomousContinuationCapsExceeded as exc:
        return {
            "allowed": False,
            "reason": exc.reason,
            "plan_id": plan_id_str,
            "plan_turns": -1,
            "plan_cap": per_plan_cap,
            "session_turns": -1,
            "session_cap": per_session_cap,
            **exc.details,
        }

    plan_turns = (
        _count_autonomous_turns_from_events(events, plan_id=plan_id_str)
        if plan_id_str
        else 0
    )
    session_turns = _count_autonomous_turns_from_events(events, plan_id=None)

    reason: str | None = None
    if plan_id_str and plan_turns >= per_plan_cap:
        reason = "per_plan_cap_reached"
    elif session_turns >= per_session_cap:
        reason = "per_session_cap_reached"

    return {
        "allowed": reason is None,
        "reason": reason,
        "plan_id": plan_id_str,
        "plan_turns": plan_turns,
        "plan_cap": per_plan_cap,
        "session_turns": session_turns,
        "session_cap": per_session_cap,
    }


def record_autonomous_turn(
    *,
    session_api: Any,
    session_id: str,
    agent_id: str,
    plan_id: str,
    trace_id: str | None,
) -> None:
    """Emit the durable `autonomous_turn.fired` event."""
    if not session_api or not session_id:
        raise AutonomousContinuationCapsExceeded(
            reason="counter_unavailable",
            details={
                "cause": "missing_session_api_or_session_id",
                "session_id": str(session_id or ""),
            },
        )
    append_event = getattr(session_api, "append_event", None)
    if not callable(append_event):
        raise AutonomousContinuationCapsExceeded(
            reason="counter_unavailable",
            details={
                "cause": "session_api_missing_append_event",
                "session_id": str(session_id or ""),
            },
        )
    events = _read_session_events_strict(session_api=session_api, session_id=session_id)
    turn_index = _count_autonomous_turns_from_events(events, plan_id=None) + 1
    try:
        append_event(
            session_id,
            AUTONOMOUS_TURN_FIRED_EVENT,
            {
                "plan_id": str(plan_id or "").strip(),
                "turn_index": turn_index,
            },
            actor_type="system",
            actor_id=str(agent_id or "").strip() or None,
            trace={"trace_id": trace_id} if trace_id else None,
            importance=2,
            redaction="none",
            status="ok",
        )
    except Exception as exc:  # noqa: BLE001
        raise AutonomousContinuationCapsExceeded(
            reason="counter_append_failed",
            details={
                "cause": str(exc),
                "session_id": str(session_id or ""),
                "plan_id": str(plan_id or ""),
                "attempted_turn_index": turn_index,
            },
        ) from exc


def should_schedule_continuation(
    *,
    runner: "BrainRunner",
    session_id: str,
    plan_id: str | None,
    signal_set: bool,
    max_per_plan: int = DEFAULT_MAX_AUTONOMOUS_TURNS_PER_PLAN,
    max_per_session: int = DEFAULT_MAX_AUTONOMOUS_TURNS_PER_SESSION,
) -> dict[str, Any]:
    """High-level decision used by CTGP-03: schedule a follow-up turn?"""
    if not signal_set:
        return {
            "allowed": False,
            "reason": "signal_not_set",
            "plan_id": str(plan_id or ""),
            "plan_turns": 0,
            "plan_cap": max(0, int(max_per_plan or 0)),
            "session_turns": 0,
            "session_cap": max(0, int(max_per_session or 0)),
        }
    plan_id_str = str(plan_id or "").strip()
    if not plan_id_str:
        return {
            "allowed": False,
            "reason": "no_active_plan",
            "plan_id": "",
            "plan_turns": 0,
            "plan_cap": max(0, int(max_per_plan or 0)),
            "session_turns": 0,
            "session_cap": max(0, int(max_per_session or 0)),
        }
    return check_autonomous_continuation_caps(
        session_api=getattr(runner, "session_api", None),
        session_id=session_id,
        plan_id=plan_id_str,
        max_per_plan=max_per_plan,
        max_per_session=max_per_session,
    )


_ELIGIBLE_PLAN_EVENT_TYPES = (
    "task_plan.declared",
    "task_plan.step_completed",
    "task_plan.revised",
)


def _signal_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("event_type") or "").strip()
    if event_type not in _ELIGIBLE_PLAN_EVENT_TYPES:
        return None
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return None
    if event_type in ("task_plan.declared", "task_plan.revised"):
        plan = payload.get("plan") or {}
        if not isinstance(plan, dict):
            return None
        return {
            "plan_id": str(plan.get("plan_id") or "").strip(),
            "continue_plan_autonomously": bool(
                plan.get("continue_plan_autonomously") or False
            ),
        }
    return {
        "plan_id": str(payload.get("plan_id") or "").strip(),
        "continue_plan_autonomously": bool(
            payload.get("continue_plan_autonomously") or False
        ),
    }


def peek_latest_continuation_signal(
    *,
    session_api: Any,
    session_id: str,
) -> dict[str, Any] | None:
    """Inspect the event log for the most recent eligible plan event."""
    if not session_api or not session_id:
        return None
    lister = getattr(session_api, "list_events", None)
    if not callable(lister):
        return None
    try:
        events = lister(session_id)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(events, list):
        return None

    # Walk events in reverse to find the most recent plan-lifecycle event.
    terminal_event_types = {
        "task_plan.step_blocked",
        "task_plan.abandoned",
        "task_plan.completed",
    }
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        if event_type in terminal_event_types:
            # Terminal event seen first (walking backwards) — it cancels
            # any older continuation signal. Runtime stops.
            return None
        if event_type in _ELIGIBLE_PLAN_EVENT_TYPES:
            return _signal_from_event(event)
    return None


def run_with_autonomous_continuation(
    runner: "BrainRunner",
    *,
    session_id: str,
    user_input: str | None = None,
    trace_id: str | None = None,
    forced_tools: list[str] | None = None,
    capability_category: str | None = None,
    max_per_plan: int = DEFAULT_MAX_AUTONOMOUS_TURNS_PER_PLAN,
    max_per_session: int = DEFAULT_MAX_AUTONOMOUS_TURNS_PER_SESSION,
    progress_callback: Any | None = None,
    approval_callback: Any | None = None,
    initial_trigger: str = "user_input",
) -> Any:
    """Run one initial turn, then auto-schedule follow-up turns while allowed."""
    result = runner.run(
        session_id=session_id,
        user_input=user_input,
        trace_id=trace_id,
        forced_tools=forced_tools,
        capability_category=capability_category,
        trigger=initial_trigger,
        progress_callback=progress_callback,
        approval_callback=approval_callback,
    )

    session_api = getattr(runner, "session_api", None)
    agent_id = getattr(getattr(runner, "profile", None), "agent_id", "") or ""

    while True:
        signal = peek_latest_continuation_signal(
            session_api=session_api, session_id=session_id
        )
        if signal is None or not signal.get("continue_plan_autonomously"):
            break
        plan_id = str(signal.get("plan_id") or "").strip()
        decision = should_schedule_continuation(
            runner=runner,
            session_id=session_id,
            plan_id=plan_id,
            signal_set=True,
            max_per_plan=max_per_plan,
            max_per_session=max_per_session,
        )
        if not decision["allowed"]:
            _emit_continuation_stopped(
                runner=runner,
                session_id=session_id,
                decision=decision,
                trace_id=getattr(
                    getattr(result, STATE_KEY_WORKING, None), "trace_id", None
                ),
            )
            break
        # Fail closed if the durable cap counter cannot be written.
        try:
            record_autonomous_turn(
                session_api=session_api,
                session_id=session_id,
                agent_id=agent_id,
                plan_id=plan_id,
                trace_id=getattr(
                    getattr(result, STATE_KEY_WORKING, None), "trace_id", None
                ),
            )
        except AutonomousContinuationCapsExceeded as exc:
            _emit_continuation_stopped(
                runner=runner,
                session_id=session_id,
                decision={
                    "reason": exc.reason,
                    "plan_turns": decision.get("plan_turns", 0),
                    "plan_cap": decision.get("plan_cap", 0),
                    "session_turns": decision.get("session_turns", 0),
                    "session_cap": decision.get("session_cap", 0),
                    "plan_id": plan_id,
                    **exc.details,
                },
                trace_id=getattr(
                    getattr(result, STATE_KEY_WORKING, None), "trace_id", None
                ),
            )
            break
        result = runner.run(
            session_id=session_id,
            user_input=None,
            trace_id=None,  # fresh trace per autonomous turn
            forced_tools=None,
            capability_category=None,
            trigger="plan_continuation",
            progress_callback=progress_callback,
            approval_callback=approval_callback,
        )
    return result


def _emit_continuation_stopped(
    *,
    runner: "BrainRunner",
    session_id: str,
    decision: dict[str, Any],
    trace_id: str | None,
) -> None:
    """Telemetry for cap-hit termination of the continuation loop."""
    try:
        from openminion.modules.brain.diagnostics.events import (
            CanonicalEventLogger,
        )

        logger = CanonicalEventLogger(
            session_api=getattr(runner, "session_api", None),
            session_id=session_id,
            agent_id=getattr(getattr(runner, "profile", None), "agent_id", "") or "",
        )
        payload: dict[str, Any] = {
            "reason": str(decision.get("reason") or "unknown"),
            "plan_turns": int(decision.get("plan_turns") or 0),
            "plan_cap": int(decision.get("plan_cap") or 0),
            "session_turns": int(decision.get("session_turns") or 0),
            "session_cap": int(decision.get("session_cap") or 0),
            "plan_id": str(decision.get("plan_id") or ""),
        }
        cause = decision.get("cause")
        if cause is not None:
            cause_text = str(cause)
            if len(cause_text) > _STOPPED_CAUSE_MAX_CHARS:
                cause_text = cause_text[:_STOPPED_CAUSE_MAX_CHARS] + "…"
            payload["counter_error"] = cause_text
        attempted_turn_index = decision.get("attempted_turn_index")
        if attempted_turn_index is not None:
            try:
                payload["attempted_turn_index"] = int(attempted_turn_index)
            except (TypeError, ValueError):
                pass
        logger.emit(
            "brain.autonomous_continuation.stopped",
            payload,
            trace_id=trace_id,
        )
    except Exception:  # noqa: BLE001 — telemetry is best-effort
        return
