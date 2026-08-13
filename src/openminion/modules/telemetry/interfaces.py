from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

from .schemas import TelemetryEvent, SessionTelemetry, CostSummary


TELEMETRY_INTERFACE_VERSION = "v1"
TELEMETRY_EXPORT_PROBE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class TelemetryExportProbeResult:
    created: bool
    transport: str
    flush: str
    cleanup: str = "completed"
    recording_sink: bool = False


def ensure_telemetry_interface_compatibility(actual_version: str) -> bool:
    if actual_version == TELEMETRY_INTERFACE_VERSION:
        return True
    raise ValueError(
        f"Telemetry interface version mismatch: expected {TELEMETRY_INTERFACE_VERSION}, got {actual_version}"
    )


@dataclass
class TelemetryContractConfig:
    db_path: Optional[str] = None
    home_root: Optional[str | Path] = None
    env: Optional[Mapping[str, str]] = None


class TelemetryContract(Protocol):
    def __init__(
        self,
        db_path: Optional[str] = ...,
        *,
        home_root: Optional[str | Path] = ...,
        env: Optional[Mapping[str, str]] = ...,
    ) -> None: ...

    async def close(self) -> None: ...

    async def record_event(self, event: TelemetryEvent) -> bool: ...

    def record_event_sync(self, event: TelemetryEvent) -> bool: ...

    async def record_metric(
        self, name: str, value: float, tags: Optional[dict[str, str]] = ...
    ) -> None: ...

    async def get_session_summary(self, session_id: str) -> SessionTelemetry: ...

    async def get_module_summary(self, session_id: str) -> dict[str, Any]: ...

    async def get_session_cost(
        self,
        session_id: str,
        provider: str = ...,
        model: str = ...,
    ) -> CostSummary: ...

    def get_path_debug(self) -> dict[str, Any]: ...


class TelemetryExporter(Protocol):
    """External export boundary for canonical telemetry events.

    Implementations own their failure handling; the local service does not
    hide exporter exceptions.
    """

    def export(self, event: TelemetryEvent) -> bool: ...

    def delete_pending_invocation(self, invocation_id: str) -> int: ...

    def probe(
        self,
        event: TelemetryEvent,
        timeout_seconds: float,
    ) -> TelemetryExportProbeResult: ...

    def close(self) -> None: ...


class TelemetryAdapterContract(Protocol):
    def __init__(self, service: TelemetryContract) -> None: ...

    def bind_execution(
        self,
        session_id: str,
        turn_id: str,
        *,
        invocation_id: str,
        execution_id: str,
        agent_id: str,
    ) -> None: ...

    def unbind_execution(self, session_id: str, turn_id: str) -> None: ...

    async def emit_tick(
        self, session_id: str, turn_id: str, elapsed_ms: float, mode: str | None = ...
    ) -> None: ...

    async def emit_tool_call(
        self,
        session_id: str,
        turn_id: str,
        tool_name: str,
        success: bool,
        mode: str | None = ...,
    ) -> None: ...

    async def emit_llm_call(
        self,
        session_id: str,
        turn_id: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = ...,
        mode: str | None = ...,
    ) -> None: ...

    async def emit_context_pack(
        self, session_id: str, turn_id: str, tokens: int, mode: str | None = ...
    ) -> None: ...

    async def emit_module_stats(
        self,
        session_id: str,
        turn_id: str,
        module_id: str,
        *,
        status: str = ...,
        latency_ms: float = ...,
        input_tokens: int = ...,
        output_tokens: int = ...,
        cached_tokens: int = ...,
        dropped_items: int = ...,
        truncated_items: int = ...,
        extra: Optional[dict[str, Any]] = ...,
        mode: str | None = ...,
    ) -> None: ...

    async def emit_module_operation(
        self,
        session_id: str,
        turn_id: str,
        module_id: str,
        operation: str,
        *,
        count: int = ...,
        status: str = ...,
        latency_ms: float = ...,
        extra: Optional[dict[str, Any]] = ...,
        mode: str | None = ...,
    ) -> None: ...

    async def emit_module_counter(
        self,
        session_id: str,
        turn_id: str,
        module_id: str,
        counter_name: str,
        value: float,
        *,
        status: str = ...,
        extra: Optional[dict[str, Any]] = ...,
        mode: str | None = ...,
    ) -> None: ...

    async def emit_tool_exec_operation(
        self,
        session_id: str,
        turn_id: str,
        operation: str,
        *,
        count: int = ...,
        success: bool = ...,
        latency_ms: float = ...,
        extra: Optional[dict[str, Any]] = ...,
        mode: str | None = ...,
    ) -> None: ...

    async def emit_canonical_event(
        self,
        session_id: str,
        turn_id: str,
        event_type: str,
        payload: Optional[dict[str, Any]] = ...,
        *,
        trace_id: str | None = ...,
        actor_type: str | None = ...,
        status: str | None = ...,
        error: Optional[dict[str, Any]] = ...,
        mode: str | None = ...,
        event_id: str | None = ...,
        timestamp: float | None = ...,
        trace_key: str | None = ...,
        invocation_id: str | None = ...,
        execution_id: str | None = ...,
        agent_id: str | None = ...,
    ) -> bool: ...

    def emit_canonical_event_sync(
        self,
        session_id: str,
        turn_id: str,
        event_type: str,
        payload: Optional[dict[str, Any]] = ...,
        *,
        trace_id: str | None = ...,
        actor_type: str | None = ...,
        status: str | None = ...,
        error: Optional[dict[str, Any]] = ...,
        mode: str | None = ...,
        event_id: str | None = ...,
        timestamp: float | None = ...,
        trace_key: str | None = ...,
        invocation_id: str | None = ...,
        execution_id: str | None = ...,
        agent_id: str | None = ...,
    ) -> bool: ...
