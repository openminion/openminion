from abc import ABC, abstractmethod

from ..schemas import CompressionResult


class CompressTelemetryStore(ABC):
    """Abstract base for compression telemetry storage implementations."""

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def record_run(
        self,
        request_id: str,
        result: CompressionResult,
        *,
        run_id: str | None = None,
    ) -> str: ...

    @abstractmethod
    def record_failure(
        self,
        request_id: str,
        error_code: str,
        message: str,
        *,
        failure_id: str | None = None,
    ) -> str: ...

    @abstractmethod
    def get_run(self, run_id: str) -> object | None: ...

    @abstractmethod
    def get_dropped_reasons(self, run_id: str) -> list[object]: ...

    @abstractmethod
    def get_explain_payload(self, run_id: str) -> object | None: ...
