"""Privacy-safe telemetry for terminal-surface migration counters."""

from __future__ import annotations

from typing import Any

from openminion.modules.telemetry.events import catalog
from openminion.modules.telemetry.schemas import TelemetryEvent

_EVENT_TYPES = {
    "launch": catalog.CLI_SURFACE_USED,
    "deprecation": catalog.CLI_DEPRECATION_SHOWN,
    "tab": catalog.CLI_DASHBOARD_TAB_ACTIVATED,
}
_SURFACES = frozenset({"chat", "dashboard", "focus", "tui", "tui-dashboard"})
_DASHBOARD_TABS = frozenset(
    {
        "agents",
        "chat",
        "cron",
        "memory",
        "monitor",
        "policy",
        "sessions",
        "system",
        "tasks",
        "third-brain",
    }
)


def record_surface_event(
    runtime: Any,
    *,
    surface: str,
    action: str,
    tab: str | None = None,
) -> bool:
    """Record a bounded migration counter without user-derived data."""
    normalized_surface = str(surface or "").strip().lower()
    normalized_action = str(action or "").strip().lower()
    normalized_tab = str(tab or "").strip().lower().removeprefix("tab-")
    if normalized_surface not in _SURFACES or normalized_action not in _EVENT_TYPES:
        return False
    if normalized_action == "tab" and normalized_tab not in _DASHBOARD_TABS:
        return False
    if normalized_action != "tab" and normalized_tab:
        return False

    api_runtime = getattr(runtime, "api_runtime", runtime)
    service = getattr(api_runtime, "telemetry_service", None)
    if service is None:
        return False
    data = {"surface": normalized_surface}
    if normalized_tab:
        data["tab"] = normalized_tab
    session_id = str(getattr(runtime, "session_id", "") or "cli-surface")
    try:
        service.record_event_sync(
            TelemetryEvent(
                session_id=session_id,
                turn_id="surface",
                event_type=_EVENT_TYPES[normalized_action],
                data=data,
            )
        )
    except Exception:
        return False
    return True
