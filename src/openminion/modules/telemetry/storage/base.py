from abc import ABC, abstractmethod
from typing import Any


class TelemetryStore(ABC):
    """Abstract base for telemetry storage implementations."""

    @abstractmethod
    def insert_event(
        self,
        *,
        session_id: str,
        turn_id: str,
        event_type: str,
        timestamp: float,
        data: dict[str, Any],
    ) -> None: ...

    @abstractmethod
    def fetch_session_events(
        self, session_id: str
    ) -> list[tuple[str, str, float, str]]: ...

    @abstractmethod
    def close(self) -> None: ...
