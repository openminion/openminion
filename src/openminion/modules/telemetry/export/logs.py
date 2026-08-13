from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..schemas import TelemetryEvent


@dataclass(frozen=True)
class OTelLogProjection:
    record_type: str
    event_name: str
    severity: str
    body: str
    attributes: dict[str, Any]


_DIAGNOSTIC_EVENTS = frozenset(
    {
        "llm.call.failed",
        "module.debug.failure",
        "telemetry.export.failed",
        "telemetry.propagation.invalid",
        "telemetry.export.probe",
    }
)
_NAMED_EVENT_TYPES = frozenset(
    {
        "auth_denied",
        "approval_required",
        "business.outcome.recorded",
        "policy_denied",
        "runtime.violation",
        "security_warning",
        "tool.execution.failed",
    }
)


def log_projection_for_event(
    event: TelemetryEvent,
    *,
    attributes: dict[str, Any],
) -> OTelLogProjection | None:
    event_type = str(event.event_type or "").strip()
    payload = event.data
    status = str(payload.get("status") or "").strip().lower()
    if event_type == "tool.execution.completed" and not bool(
        payload.get("audit_enabled", False)
    ):
        return None
    if event_type in _DIAGNOSTIC_EVENTS:
        severity = "ERROR"
        if event_type == "telemetry.export.probe":
            severity = "INFO"
        elif event_type == "telemetry.propagation.invalid":
            severity = "WARN"
        return OTelLogProjection(
            record_type="LogRecord",
            event_name=event_type,
            severity=severity,
            body=event_type,
            attributes=attributes,
        )
    if (
        event_type in _NAMED_EVENT_TYPES
        or event_type.startswith("policy.")
        or event_type.startswith("safety.")
        or event_type.startswith("agent.handoff.")
        or (
            event_type == "tool.execution.completed"
            and bool(payload.get("audit_enabled", False))
        )
    ):
        severity = "ERROR" if status in {"denied", "error", "failed"} else "INFO"
        return OTelLogProjection(
            record_type="EventRecord",
            event_name=event_type,
            severity=severity,
            body=event_type,
            attributes=attributes,
        )
    return None


__all__ = ["OTelLogProjection", "log_projection_for_event"]
