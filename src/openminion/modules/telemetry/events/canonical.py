from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from openminion.modules.telemetry.schemas import TelemetryEvent


def build_canonical_event(
    *,
    event_factory: Callable[..., TelemetryEvent],
    bound_correlation: Mapping[str, str],
    session_id: str,
    turn_id: str,
    event_type: str,
    payload: dict[str, Any] | None,
    trace_id: str | None,
    actor_type: str | None,
    status: str | None,
    error: dict[str, Any] | None,
    mode: str | None,
    event_id: str | None,
    timestamp: float | None,
    trace_key: str | None,
    invocation_id: str | None,
    execution_id: str | None,
    agent_id: str | None,
) -> TelemetryEvent:
    explicit = any(
        value is not None
        for value in (
            event_id,
            timestamp,
            trace_key,
            invocation_id,
            execution_id,
            agent_id,
        )
    )
    correlation = {} if explicit else dict(bound_correlation)
    event_payload = {**correlation, **dict(payload or {})}
    for name, value in (
        ("trace_id", trace_id),
        ("actor_type", actor_type),
        ("status", status),
        ("error", dict(error) if error else None),
    ):
        if value:
            event_payload.setdefault(name, value)
    if event_type.startswith("agent.invocation."):
        if not str(event_id or "").strip():
            raise ValueError("invocation lifecycle event_id must be non-empty")
        if timestamp is None or not math.isfinite(float(timestamp)):
            raise ValueError("invocation lifecycle timestamp must be finite")
        if not str(invocation_id or "").strip():
            raise ValueError("invocation lifecycle invocation_id must be non-empty")
    return event_factory(
        session_id=session_id,
        turn_id=turn_id,
        event_type=event_type,
        mode=mode,
        data=event_payload,
        event_id=str(event_id or ""),
        timestamp=timestamp,
        trace_key=trace_key,
        invocation_id=invocation_id,
        execution_id=execution_id,
        agent_id=agent_id,
        use_bound_context=False,
    )
