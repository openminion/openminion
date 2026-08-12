from abc import ABC, abstractmethod
from dataclasses import dataclass

from openminion.modules.telemetry.schemas import TelemetryEvent


class TelemetryEventConflictError(RuntimeError):
    """Raised when an event ID is reused for different structural facts."""


@dataclass(frozen=True)
class TelemetryEventPageRow:
    row_id: int
    event: TelemetryEvent
    timestamp_valid: bool = True


def telemetry_event_sort_key(
    row: TelemetryEventPageRow,
) -> tuple[float, str, int]:
    return row.event.timestamp, str(row.event.event_id or ""), row.row_id


class TelemetryStore(ABC):
    """Abstract base for telemetry storage implementations."""

    @abstractmethod
    def insert_event(self, event: TelemetryEvent) -> None: ...

    @abstractmethod
    def insert_event_if_absent(self, event: TelemetryEvent) -> bool: ...

    @abstractmethod
    def fetch_session_events(self, session_id: str) -> list[TelemetryEvent]: ...

    @abstractmethod
    def fetch_invocation_events(self, invocation_id: str) -> list[TelemetryEvent]: ...

    @abstractmethod
    def fetch_execution_events(self, execution_id: str) -> list[TelemetryEvent]: ...

    @abstractmethod
    def fetch_events(self) -> list[TelemetryEvent]: ...

    @abstractmethod
    def event_high_water(
        self,
        *,
        invocation_id: str | None = None,
    ) -> int: ...

    @abstractmethod
    def fetch_event_page(
        self,
        *,
        high_water: int,
        limit: int,
        before_timestamp: float | None = None,
        before_id: int | None = None,
        invocation_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        event_types: tuple[str, ...] = (),
    ) -> list[TelemetryEventPageRow]: ...

    @abstractmethod
    def find_turn_invocation_ids(
        self,
        *,
        session_id: str,
        turn_id: str,
        high_water: int | None = None,
        limit: int = 2,
    ) -> list[str]: ...

    @abstractmethod
    def delete_invocation_events(self, invocation_id: str) -> int: ...

    @abstractmethod
    def close(self) -> None: ...
