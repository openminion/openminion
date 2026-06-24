"""Registry interface contract helpers."""

from __future__ import annotations

from typing import Any, ClassVar, Protocol


REGISTRY_INTERFACE_VERSION = "v1"
_REQUIRED_REGISTRY_METHODS = (
    "load",
    "reload",
    "close",
    "list",
    "get",
    "register",
    "unregister",
    "find_by_method",
    "find_by_capability",
    "resolve_method",
    "resolve_agent",
    "heartbeat",
    "get_status",
    "set_status",
    "explain_resolution",
)


class RegistryInterfaceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AgentRegistryInterface(Protocol):
    """Agent Registry interface contract."""

    contract_version: ClassVar[str] = REGISTRY_INTERFACE_VERSION

    def __init__(
        self,
        *,
        manifest_path: str = "agents.yaml",
        store: Any,  # RegistryStore
        allow_runtime_override: bool = True,
        builtin_agents: list[Any] | None = None,  # List[AgentDescriptor]
    ) -> None: ...

    def load(self) -> None: ...

    def reload(self) -> None: ...

    def close(self) -> None: ...

    def list(
        self, filters: dict[str, Any] | None = None
    ) -> list[Any]: ...  # List[AgentDescriptor]

    def get(self, agent_id: str) -> Any | None: ...  # AgentDescriptor | None

    def register(
        self,
        descriptor: Any | dict[str, Any],
        source: str = "runtime",
        overwrite: bool = False,
    ) -> None: ...  # AgentDescriptor

    def unregister(self, agent_id: str) -> None: ...

    def find_by_method(
        self, method: str, filters: dict[str, Any] | None = None
    ) -> list[Any]: ...  # List[AgentDescriptor]

    def find_by_capability(
        self, capability: str, filters: dict[str, Any] | None = None
    ) -> list[Any]: ...  # List[AgentDescriptor]

    def resolve_method(
        self,
        method: str,
        constraints: dict[str, Any] | Any | None = None,  # ResolveConstraints
    ) -> Any | None: ...  # ResolvedRoute | None

    def resolve_agent(
        self,
        agent_id: str,
        method: str | None = None,
        constraints: dict[str, Any] | Any | None = None,  # ResolveConstraints
    ) -> Any | None: ...  # ResolvedRoute | None

    def heartbeat(self, agent_id: str, status_patch: dict[str, Any]) -> None: ...

    def get_status(self, agent_id: str) -> Any: ...  # AgentStatus

    def set_status(
        self, agent_id: str, state: str, error: dict[str, Any] | None = None
    ) -> None: ...

    def explain_resolution(
        self,
        method: str,
        constraints: dict[str, Any] | Any | None = None,  # ResolveConstraints
    ) -> dict[str, Any]: ...


def ensure_registry_compatibility(
    registry: Any, strict: bool = True
) -> tuple[bool, list[str]]:
    """Validate agent registry implements the required interface."""
    errors: list[str] = []
    version = getattr(registry, "contract_version", None)
    if version is None:
        errors.append("Missing contract_version attribute")
    elif version != REGISTRY_INTERFACE_VERSION:
        errors.append(
            f"Version mismatch: expected {REGISTRY_INTERFACE_VERSION}, got {version}"
        )

    errors.extend(
        f"Missing required method: {method}"
        for method in _REQUIRED_REGISTRY_METHODS
        if not callable(getattr(registry, method, None))
    )

    if errors:
        if strict:
            raise RegistryInterfaceError(
                "REGISTRY_INTERFACE_VIOLATION", f"Registry incompatible: {errors}"
            )
        return False, errors

    return True, []
