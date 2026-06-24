from typing import Any, Protocol


class ToolCatalog(Protocol):
    """Read-only view over the set of tools available to the brain."""

    def list_tool_names(self) -> set[str]: ...

    def list_tool_schemas(self) -> list[dict[str, Any]]: ...

    def get_tool_schema(self, name: str) -> dict[str, Any] | None: ...


__all__ = ["ToolCatalog"]
