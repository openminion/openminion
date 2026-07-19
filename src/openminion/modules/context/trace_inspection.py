from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from openminion.modules.telemetry.events.catalog import CONTEXT_MANIFEST_CREATED


CONTEXT_TRACE_NOT_FOUND = "CONTEXT_TRACE_NOT_FOUND"
CONTEXT_TRACE_PERSISTENCE_FAILED = "CONTEXT_TRACE_PERSISTENCE_FAILED"


@dataclass(frozen=True)
class ContextTraceLookupError(RuntimeError):
    message: str
    code: str
    reason_code: str = ""

    def __str__(self) -> str:
        return self.message


def list_context_traces(
    sessions: Any,
    *,
    session_id: str,
    turn_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise ContextTraceLookupError(
            "`session_id` is required.",
            code="invalid_request",
        )

    if getattr(sessions, "get_session", None) is not None:
        session = sessions.get_session(normalized_session_id)
        if session is None:
            raise ContextTraceLookupError(
                f"Session '{normalized_session_id}' was not found.",
                code="session_not_found",
            )

    list_events = getattr(sessions, "list_events", None)
    if not callable(list_events):
        raise ContextTraceLookupError(
            "Session store does not expose event inspection.",
            code=CONTEXT_TRACE_NOT_FOUND,
        )

    safe_limit = max(1, min(int(limit or 50), 500))
    events = list_events(
        normalized_session_id,
        event_type=CONTEXT_MANIFEST_CREATED,
        limit=safe_limit,
    )
    traces = [
        trace
        for event in events
        if (trace := _trace_from_event(event, turn_id=turn_id)) is not None
    ]
    if not traces:
        raise ContextTraceLookupError(
            f"No context decision trace found for session '{normalized_session_id}'.",
            code=CONTEXT_TRACE_NOT_FOUND,
        )
    return {
        "session_id": normalized_session_id,
        "turn_id": str(turn_id or "").strip() or None,
        "traces": traces,
        "count": len(traces),
        "limit": safe_limit,
    }


def _trace_from_event(
    event: Mapping[str, Any],
    *,
    turn_id: str | None,
) -> dict[str, Any] | None:
    payload = dict(event.get("payload", {}) or {})
    trace = payload.get("decision_trace")
    if not isinstance(trace, dict):
        return None
    normalized_turn_id = str(turn_id or "").strip()
    if normalized_turn_id and str(trace.get("turn_id", "") or "") != normalized_turn_id:
        return None
    return {
        "event_id": str(event.get("id") or event.get("event_id") or ""),
        "event_type": str(event.get("event_type") or event.get("type") or ""),
        "created_at": str(event.get("created_at") or event.get("timestamp") or ""),
        "decision_trace": trace,
    }


__all__ = [
    "CONTEXT_TRACE_NOT_FOUND",
    "CONTEXT_TRACE_PERSISTENCE_FAILED",
    "ContextTraceLookupError",
    "list_context_traces",
]
