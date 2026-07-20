"""Privacy-safe telemetry for the canonical interactive surface."""

from __future__ import annotations

from typing import Any

from openminion.modules.telemetry.events import catalog
from openminion.modules.telemetry.schemas import TelemetryEvent


def record_surface_event(runtime: Any) -> bool:
    """Record one canonical interactive launch without user-derived data."""
    api_runtime = getattr(runtime, "api_runtime", runtime)
    service = getattr(api_runtime, "telemetry_service", None)
    if service is None:
        return False
    session_id = str(getattr(runtime, "session_id", "") or "cli-surface")
    try:
        service.record_event_sync(
            TelemetryEvent(
                session_id=session_id,
                turn_id="surface",
                event_type=catalog.CLI_SURFACE_USED,
                data={"surface": "interactive"},
            )
        )
    except Exception:
        return False
    return True
