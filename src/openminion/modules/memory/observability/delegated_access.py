"""Bridge Sophiagraph access metrics into OpenMinion's telemetry owner."""

from __future__ import annotations

from dataclasses import dataclass

from openminion.modules.telemetry.schemas import TelemetryEvent
from sophiagraph.access import MemoryAccessTelemetryEvent


@dataclass(slots=True)
class DelegatedMemoryTelemetryBridge:
    """Callable recorder that emits identity-free module telemetry."""

    telemetry_service: object
    session_id: str
    turn_id: str

    def __call__(self, event: MemoryAccessTelemetryEvent) -> None:
        recorder = getattr(self.telemetry_service, "record_event_sync", None)
        if not callable(recorder):
            return
        recorder(
            TelemetryEvent(
                session_id=self.session_id,
                turn_id=self.turn_id,
                event_type="metric",
                data={
                    "module_id": "openminion-memory",
                    "operation": f"delegated_access.{event.operation}",
                    "status": "ok" if event.outcome == "allow" else "blocked",
                    "outcome": event.outcome,
                    "reason": event.reason,
                    "resolver_outcome": event.resolver_outcome,
                    "resolver_duration_ms": event.resolver_duration_ms,
                    "omitted_count": event.omitted_count,
                    "effective_max_results": event.effective_max_results,
                    "effective_max_context_tokens": event.effective_max_context_tokens,
                },
            )
        )


__all__ = ["DelegatedMemoryTelemetryBridge"]
