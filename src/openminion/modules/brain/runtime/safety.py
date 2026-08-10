import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from openminion.modules.telemetry.events.module import emit_module_telemetry

SAFETY_INTERFACE_VERSION = "v1"


def ensure_safety_interface_compatibility(actual_version: str) -> bool:
    """Validate that the safety interface version is compatible."""
    if actual_version == SAFETY_INTERFACE_VERSION:
        return True
    raise ValueError(
        f"Safety interface version mismatch: expected {SAFETY_INTERFACE_VERSION}, got {actual_version}"
    )


class SafetyAction(str, Enum):
    """Safety action types."""

    STOP = "stop"
    KILL = "kill"
    PANIC = "panic"


class SafetyState(str, Enum):
    """Safety state values."""

    NORMAL = "normal"
    STOPPING = "stopping"
    STOPPED = "stopped"
    KILLING = "killing"
    KILLED = "killed"
    PANICKING = "panicking"
    PANICKED = "panicked"


@dataclass
class SafetyEvent:
    """Record of a safety action."""

    action: SafetyAction
    state_before: SafetyState
    state_after: SafetyState
    reason: str
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SafetyContract(Protocol):
    """Protocol defining the safety interface contract."""

    def __init__(self) -> None: ...

    @property
    def state(self) -> SafetyState: ...

    def is_normal(self) -> bool: ...

    def stop(
        self,
        *,
        session_id: str | None = ...,
        reason: str = ...,
        metadata: dict[str, Any] | None = ...,
    ) -> bool: ...

    def kill(
        self,
        *,
        session_id: str | None = ...,
        reason: str = ...,
        metadata: dict[str, Any] | None = ...,
    ) -> bool: ...

    def panic(
        self,
        *,
        session_id: str | None = ...,
        reason: str = ...,
        metadata: dict[str, Any] | None = ...,
    ) -> bool: ...

    def reset(self) -> None: ...

    def get_events(self) -> list[SafetyEvent]: ...

    def clear_events(self) -> None: ...


class SafetyService:
    """Runtime skeleton for safety control."""

    def __init__(self, *, telemetryctl: Any | None = None) -> None:
        self._state = SafetyState.NORMAL
        self._lock = threading.RLock()
        self._events: list[SafetyEvent] = []
        self._telemetryctl = telemetryctl

    @property
    def contract_version(self) -> str:
        """Interface contract version for this implementation."""
        return SAFETY_INTERFACE_VERSION

    @property
    def state(self) -> SafetyState:
        """Current safety state."""
        with self._lock:
            return self._state

    def is_normal(self) -> bool:
        """Return whether the service is in the normal state."""
        return self.state == SafetyState.NORMAL

    def stop(
        self,
        *,
        session_id: str | None = None,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Request graceful stop."""
        with self._lock:
            if self._state != SafetyState.NORMAL:
                return False
            self._state = SafetyState.STOPPING
            self._events.append(
                SafetyEvent(
                    action=SafetyAction.STOP,
                    state_before=SafetyState.NORMAL,
                    state_after=SafetyState.STOPPING,
                    reason=reason,
                    session_id=session_id,
                    metadata=dict(metadata or {}),
                )
            )
            self._emit_event(self._events[-1])
            self._state = SafetyState.STOPPED
            return True

    def kill(
        self,
        *,
        session_id: str | None = None,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Request immediate termination."""
        with self._lock:
            if self._state in {SafetyState.KILLED, SafetyState.PANICKED}:
                return False
            old_state = self._state
            self._state = SafetyState.KILLING
            self._events.append(
                SafetyEvent(
                    action=SafetyAction.KILL,
                    state_before=old_state,
                    state_after=SafetyState.KILLING,
                    reason=reason,
                    session_id=session_id,
                    metadata=dict(metadata or {}),
                )
            )
            self._emit_event(self._events[-1])
            self._state = SafetyState.KILLED
            return True

    def panic(
        self,
        *,
        session_id: str | None = None,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Request emergency stop."""
        with self._lock:
            if self._state == SafetyState.PANICKED:
                return False
            old_state = self._state
            self._state = SafetyState.PANICKING
            self._events.append(
                SafetyEvent(
                    action=SafetyAction.PANIC,
                    state_before=old_state,
                    state_after=SafetyState.PANICKING,
                    reason=reason,
                    session_id=session_id,
                    metadata=dict(metadata or {}),
                )
            )
            self._emit_event(self._events[-1])
            self._state = SafetyState.PANICKED
            return True

    def reset(self) -> None:
        """Reset to the normal state."""
        with self._lock:
            self._state = SafetyState.NORMAL

    def get_events(self) -> list[SafetyEvent]:
        """Return all safety events."""
        with self._lock:
            return list(self._events)

    def clear_events(self) -> None:
        """Clear event history."""
        with self._lock:
            self._events.clear()

    def _emit_event(self, event: SafetyEvent) -> None:
        metadata = event.metadata
        if self._telemetryctl is None or not event.session_id:
            return
        reason_code = "".join(
            char if char.isalnum() else "_"
            for char in str(event.reason or "").strip().lower()
        )[:64].strip("_")
        emit_module_telemetry(
            self._telemetryctl,
            "emit_canonical_event",
            event.session_id,
            str(metadata.get("turn_id") or event.session_id),
            "safety.preempted",
            {
                "trace_id": str(metadata.get("trace_id") or ""),
                "invocation_id": str(metadata.get("invocation_id") or ""),
                "execution_id": str(metadata.get("execution_id") or ""),
                "action": event.action.value,
                "state_before": event.state_before.value,
                "state_after": event.state_after.value,
                "violation_category": str(
                    metadata.get("violation_category") or "runtime_safety"
                ),
                "reason_code": reason_code or "unspecified",
            },
            trace_id=str(metadata.get("trace_id") or "") or None,
            status="preempted",
            logger=__import__("logging").getLogger(__name__),
        )


__all__ = [
    "SafetyService",
    "SAFETY_INTERFACE_VERSION",
    "SafetyAction",
    "SafetyContract",
    "SafetyEvent",
    "SafetyState",
    "ensure_safety_interface_compatibility",
]
