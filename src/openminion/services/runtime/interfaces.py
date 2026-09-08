"""Contracts consumed by runtime composition services."""

from __future__ import annotations

from threading import Event
from typing import TYPE_CHECKING, Any, Callable, NamedTuple, Protocol

if TYPE_CHECKING:
    from .manager import TurnChunk


class DesktopApprovalRequest(NamedTuple):
    session_id: str
    trace_id: str
    tool_name: str
    call_id: str
    argument_keys: tuple[str, ...]
    emit_chunk: Callable[[TurnChunk], None]
    cancel_event: Event


DesktopApprovalRequester = Callable[[DesktopApprovalRequest], bool]


class RuntimeFacade(Protocol):
    config: Any
    config_path: Any
    config_manager: Any
    home_root: Any
    channels: Any
    plugins: Any
    provider: Any
    sessions: Any
    runtime_manager: Any
    run_profile_overrides: Any
    tool_workspace_root: Any
    telemetry_service: Any

    def close(self) -> None: ...

    def evict_agent_runtime(self, *, agent_id: str, reason: str) -> None: ...

    def resolve_gateway(
        self,
        agent_id: str | None = None,
        overrides: Any | None = None,
    ) -> Any: ...


__all__ = ["DesktopApprovalRequest", "DesktopApprovalRequester", "RuntimeFacade"]
