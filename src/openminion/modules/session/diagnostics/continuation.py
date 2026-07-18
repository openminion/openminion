"""Bounded operational telemetry adapter for session continuation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openminion.modules.telemetry.schemas import TelemetryEvent


def continuation_telemetry_sink(
    runtime: Any,
    *,
    session_id: str,
) -> Callable[[str, dict[str, Any]], None] | None:
    service = getattr(runtime, "telemetry_service", None)
    record_sync = getattr(service, "record_event_sync", None)
    if not callable(record_sync):
        return None

    def emit(event_type: str, data: dict[str, Any]) -> None:
        record_sync(
            TelemetryEvent(
                session_id=session_id,
                turn_id="continuation",
                event_type=event_type,
                data=dict(data),
            )
        )

    return emit


__all__ = ["continuation_telemetry_sink"]
