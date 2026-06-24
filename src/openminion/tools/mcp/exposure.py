"""Per-agent MCP exposure helpers."""

import fnmatch
from typing import Any, Mapping

from openminion.base.config.mcp import (
    MCPExposureConfig,
    MCPServerConfig,
    coerce_mcp_exposure_config,
)
from openminion.modules.tool.registry import ToolRegistry

_MCP_RUNTIME_PREFIX = "mcp."


def mcp_server_name_for_runtime_tool(tool_name: str) -> str | None:
    token = str(tool_name or "").strip()
    if not token.startswith(_MCP_RUNTIME_PREFIX):
        return None
    parts = token.split(".", 2)
    if len(parts) < 3:
        return None
    return parts[1] or None


def is_mcp_runtime_tool(tool_name: str) -> bool:
    return mcp_server_name_for_runtime_tool(tool_name) is not None


def is_mcp_tool_exposed(tool_name: str, exposure: MCPExposureConfig | None) -> bool:
    normalized = coerce_mcp_exposure_config(exposure)
    server_name = mcp_server_name_for_runtime_tool(tool_name)
    if server_name is None:
        return True
    if normalized.include_servers and server_name not in normalized.include_servers:
        return False
    if server_name in normalized.exclude_servers:
        return False
    if normalized.include_tools and not _matches_any(
        tool_name, normalized.include_tools
    ):
        return False
    return not _matches_any(tool_name, normalized.exclude_tools)


class MCPScopedToolRegistryView(ToolRegistry):
    """Read-through registry view that filters MCP tools for one agent profile."""

    def __init__(
        self, base_registry: ToolRegistry, exposure: MCPExposureConfig
    ) -> None:
        self._base_registry = base_registry
        self._mcp_exposure = coerce_mcp_exposure_config(exposure)

    @property
    def _tools(self) -> dict[str, Any]:
        tools = getattr(self._base_registry, "_tools", {})
        if not isinstance(tools, Mapping):
            return {}
        return {
            str(name): tool
            for name, tool in tools.items()
            if is_mcp_tool_exposed(str(name), self._mcp_exposure)
        }

    @property
    def _category_index(self) -> dict[str, set[str]]:
        base_index = getattr(self._base_registry, "_category_index", {})
        if not isinstance(base_index, Mapping):
            return {}
        visible_names = set(self._tools.keys())
        return {
            str(category): {str(name) for name in names if str(name) in visible_names}
            for category, names in base_index.items()
        }

    @property
    def mcp_manager(self) -> Any:
        return getattr(self._base_registry, "mcp_manager", None)

    def register(self, tool: Any) -> None:
        raise RuntimeError("MCPScopedToolRegistryView is read-only")

    def unregister(self, tool_name: str) -> None:
        raise RuntimeError("MCPScopedToolRegistryView is read-only")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base_registry, name)


def scoped_mcp_registry_view(
    registry: ToolRegistry,
    exposure: MCPExposureConfig | None,
) -> ToolRegistry:
    normalized = coerce_mcp_exposure_config(exposure)
    if normalized.is_empty:
        return registry
    return MCPScopedToolRegistryView(registry, normalized)


def build_mcp_exposure_report(
    *,
    registry: Any,
    server_configs: list[MCPServerConfig],
    exposure: MCPExposureConfig | None,
) -> dict[str, Any]:
    normalized = coerce_mcp_exposure_config(exposure)
    configured_servers = sorted({item.name for item in server_configs})
    all_runtime_tools = sorted(
        str(name)
        for name in getattr(registry, "_tools", {})
        if is_mcp_runtime_tool(str(name))
    )
    exposed_runtime_tools = [
        name for name in all_runtime_tools if is_mcp_tool_exposed(name, normalized)
    ]
    exposed_servers = sorted(
        {
            server
            for name in exposed_runtime_tools
            if (server := mcp_server_name_for_runtime_tool(name))
        }
    )
    filtered_runtime_tools = [
        name for name in all_runtime_tools if name not in set(exposed_runtime_tools)
    ]
    return {
        "configured_servers": configured_servers,
        "configured_server_count": len(configured_servers),
        "registered_runtime_tool_count": len(all_runtime_tools),
        "exposed_servers": exposed_servers,
        "exposed_runtime_tools": exposed_runtime_tools,
        "exposed_runtime_tool_count": len(exposed_runtime_tools),
        "filtered_runtime_tools": filtered_runtime_tools,
        "filtered_runtime_tool_count": len(filtered_runtime_tools),
        "filter": {
            "include_servers": list(normalized.include_servers),
            "exclude_servers": list(normalized.exclude_servers),
            "include_tools": list(normalized.include_tools),
            "exclude_tools": list(normalized.exclude_tools),
        },
    }


def _matches_any(tool_name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(tool_name, pattern) for pattern in patterns)


__all__ = [
    "MCPScopedToolRegistryView",
    "build_mcp_exposure_report",
    "is_mcp_runtime_tool",
    "is_mcp_tool_exposed",
    "mcp_server_name_for_runtime_tool",
    "scoped_mcp_registry_view",
]
