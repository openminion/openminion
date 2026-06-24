from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry


@dataclass(frozen=True)
class ToolRegisterContext:
    """Context passed to module registrars during bootstrap."""

    module_id: str
    config: Any | None = None
    workspace_root: Path | None = None
    run_root: Path | None = None
    prepared_state: Any | None = None
    strict: bool = True


@runtime_checkable
class ToolModuleRegistrar(Protocol):
    """Protocol for tool module registrars."""

    module_id: str
    is_provider_only: bool

    def get_manifest(self, ctx: ToolRegisterContext) -> Any | None:
        """Return manifest metadata for this module."""
        ...

    def register(self, registry: "ToolRegistry", ctx: ToolRegisterContext) -> None:
        """Register runtime tool objects with the registry."""
        ...


__all__ = ["ToolRegisterContext", "ToolModuleRegistrar"]
