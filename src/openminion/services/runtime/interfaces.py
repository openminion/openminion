"""Contracts consumed by runtime composition services."""

from typing import Any, Protocol


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

    def resolve_gateway(self, agent_id: str | None = None) -> Any: ...


__all__ = ["RuntimeFacade"]
