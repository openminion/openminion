from abc import ABC, abstractmethod

from openminion.modules.telemetry.schemas import TelemetryEvent


class TelemetryStore(ABC):
    """Abstract base for telemetry storage implementations."""

    @abstractmethod
    def insert_event(self, event: TelemetryEvent) -> None: ...

    @abstractmethod
    def fetch_session_events(self, session_id: str) -> list[TelemetryEvent]: ...

    @abstractmethod
    def fetch_invocation_events(self, invocation_id: str) -> list[TelemetryEvent]: ...

    @abstractmethod
    def fetch_execution_events(self, execution_id: str) -> list[TelemetryEvent]: ...

    @abstractmethod
    def fetch_events(self) -> list[TelemetryEvent]: ...

    @abstractmethod
    def delete_invocation_events(self, invocation_id: str) -> int: ...

    @abstractmethod
    def close(self) -> None: ...
