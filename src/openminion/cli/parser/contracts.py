from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable

CLI_INTERFACE_VERSION = "v1"


@runtime_checkable
class AgentRuntimeAPI(Protocol):
    """Minimal contract for the canonical interactive runtime."""

    contract_version: str  # must equal CLI_INTERFACE_VERSION

    @property
    def agent_id(self) -> str: ...
    @property
    def session_id(self) -> str: ...
    @property
    def transport(self) -> str: ...

    def get_current_history(self) -> list[Any]:
        """Return list[ChatMessage] for the current session."""
        ...

    def list_sessions(self) -> list[Any]:
        """Return list[SidebarItem]."""
        ...

    def list_agents(self) -> list[Any]:
        """Return list[SidebarItem]."""
        ...

    def list_tools(self) -> list[tuple[str, bool]]:
        """Return list of (name, enabled) pairs."""
        ...

    def switch_session(self, session_id: str) -> list[Any]:
        """Switch active session; return new session's history."""
        ...

    def switch_agent(self, agent_id: str) -> None: ...

    def new_session(self) -> str:
        """Create and activate a new session; return its id."""
        ...


@runtime_checkable
class ChatRuntimeAPI(AgentRuntimeAPI, Protocol):
    """Extends `AgentRuntimeAPI` with streaming chat turn support."""

    async def send_message(self, text: str) -> AsyncIterator[str]:
        """Yield response chunks.  Implementations must be async generators."""
        ...


_PROPERTY_MEMBERS = {"agent_id", "session_id", "transport"}

_REQUIRED: dict[str, tuple[str, ...]] = {
    "agent_runtime": (
        "agent_id",
        "session_id",
        "transport",
        "get_current_history",
        "list_sessions",
        "list_agents",
        "list_tools",
        "switch_session",
        "switch_agent",
        "new_session",
    ),
    "chat_runtime": (
        "agent_id",
        "session_id",
        "transport",
        "send_message",
        "get_current_history",
        "list_sessions",
        "list_agents",
        "list_tools",
        "switch_session",
        "switch_agent",
        "new_session",
    ),
}


def ensure_cli_component_compatibility(
    component: object,
    *,
    component_type: str,
) -> None:
    if component_type not in _REQUIRED:
        raise ValueError(
            f"unknown cli component_type {component_type!r}; valid: {sorted(_REQUIRED)}"
        )

    errors: list[str] = []
    for member in _REQUIRED[component_type]:
        if not hasattr(component, member):
            errors.append(f"missing '{member}'")
        elif member not in _PROPERTY_MEMBERS:
            val = getattr(component, member)
            if not callable(val):
                errors.append(f"'{member}' must be callable")

    version = getattr(component, "contract_version", None)
    if version != CLI_INTERFACE_VERSION:
        errors.append(
            f"contract_version mismatch: got={version!r} "
            f"expected={CLI_INTERFACE_VERSION!r}"
        )

    if errors:
        raise TypeError(
            f"{component.__class__.__name__} incompatible with "
            f"cli/{component_type} contract: {'; '.join(errors)}"
        )
