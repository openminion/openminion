from dataclasses import dataclass
from typing import Any, Protocol, Optional
from collections.abc import Mapping
from pathlib import Path
from .schemas import TelemetryEvent, SessionTelemetry, CostSummary


TELEMETRY_INTERFACE_VERSION = "v1"


def ensure_telemetry_interface_compatibility(actual_version: str) -> bool:
    """Validate that actual interface version is compatible with expected version."""
    if actual_version == TELEMETRY_INTERFACE_VERSION:
        return True
    raise ValueError(
        f"Telemetry interface version mismatch: expected {TELEMETRY_INTERFACE_VERSION}, got {actual_version}"
    )


@dataclass
class TelemetryContractConfig:
    """Configuration for telemetry service contract."""

    db_path: Optional[str] = None
    home_root: Optional[str | Path] = None
    env: Optional[Mapping[str, str]] = None


class TelemetryContract(Protocol):
    """Protocol defining the telemetry interface contract."""

    def __init__(
        self,
        db_path: Optional[str] = ...,
        *,
        home_root: Optional[str | Path] = ...,
        env: Optional[Mapping[str, str]] = ...,
    ) -> None: ...

    async def close(self) -> None: ...

    async def record_event(self, event: TelemetryEvent) -> None: ...

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


class TelemetryAdapterContract(Protocol):
    """Protocol defining the telemetry adapter interface contract."""

    def __init__(self, service: TelemetryContract) -> None: ...

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
    ) -> None: ...
