import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

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

    def stop(self, *, session_id: str | None = ..., reason: str = ...) -> bool: ...

    def kill(self, *, session_id: str | None = ..., reason: str = ...) -> bool: ...

    def panic(self, *, session_id: str | None = ..., reason: str = ...) -> bool: ...

    def reset(self) -> None: ...

    def get_events(self) -> list[SafetyEvent]: ...

    def clear_events(self) -> None: ...


class SafetyService:
    """Runtime skeleton for safety control."""

    def __init__(self) -> None:
        self._state = SafetyState.NORMAL
        self._lock = threading.RLock()
        self._events: list[SafetyEvent] = []

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

    def stop(self, *, session_id: str | None = None, reason: str = "") -> bool:
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
                )
            )
            self._state = SafetyState.STOPPED
            return True

    def kill(self, *, session_id: str | None = None, reason: str = "") -> bool:
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
                )
            )
            self._state = SafetyState.KILLED
            return True

    def panic(self, *, session_id: str | None = None, reason: str = "") -> bool:
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
                )
            )
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


__all__ = [
    "SafetyService",
    "SAFETY_INTERFACE_VERSION",
    "SafetyAction",
    "SafetyContract",
    "SafetyEvent",
    "SafetyState",
    "ensure_safety_interface_compatibility",
]
