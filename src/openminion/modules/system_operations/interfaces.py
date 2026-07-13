from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .schemas import (
    OperationTarget,
    TransportFacts,
    TransportReadResult,
    TransportResult,
)

OutputSink = Callable[[str, str], None]


class TargetTransport(Protocol):
    def connect(self, target: OperationTarget) -> TransportFacts: ...

    def inspect(self, target: OperationTarget) -> TransportFacts: ...

    def run(
        self,
        target: OperationTarget,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        operation_id: str = "",
        output_sink: OutputSink | None = None,
    ) -> TransportResult: ...

    def read(
        self,
        target: OperationTarget,
        path: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> TransportReadResult: ...

    def cancel(self, operation_id: str) -> bool: ...

    def close(self) -> None: ...
