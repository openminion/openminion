from dataclasses import dataclass
from typing import Any

from openminion.base.config.env import resolve_environment_config
from openminion.modules.tool.constants import (
    TOOL_BOOTSTRAP_GATE_ALWAYS,
    TOOL_BOOTSTRAP_GATE_NEVER,
)


@dataclass
class _ToolBootstrapEntry:
    kind: str
    module_name: str
    label: str
    required: bool = True
    gate: str = TOOL_BOOTSTRAP_GATE_ALWAYS


@dataclass
class _ToolBootstrapRecord:
    kind: str
    module_name: str
    label: str
    required: bool
    gate: str
    enabled: bool
    status: str
    error: str = ""
    added_runtime_tools: list[str] | None = None


_MCP_TOOL_BOOTSTRAP_ENTRY = _ToolBootstrapEntry(
    kind="tool",
    module_name="openminion.tools.mcp",
    label="MCP",
    required=False,
)


def _entry_enabled(entry: _ToolBootstrapEntry) -> bool:
    gate = str(entry.gate or TOOL_BOOTSTRAP_GATE_ALWAYS).strip().lower()
    if gate == TOOL_BOOTSTRAP_GATE_ALWAYS:
        return True
    if gate == TOOL_BOOTSTRAP_GATE_NEVER:
        return False
    env_val = (
        resolve_environment_config()
        .get(f"OPENMINION_TOOL_GATE_{gate.upper()}", "1")
        .strip()
    )
    return env_val in ("1", "true", "yes", "on")


def _entry_enabled_for_runtime_config(
    entry: _ToolBootstrapEntry,
    config: Any | None,
) -> bool:
    """Apply runtime-config gating for tool modules that can be disabled.

    Today this is only used for channel reactions, which should disappear from
    runtime inventories when explicitly disabled.
    """

    if entry.module_name != "openminion.tools.reaction":
        return True
    runtime_cfg = getattr(config, "runtime", config)
    if runtime_cfg is None:
        return True
    raw = getattr(runtime_cfg, "reactions_enabled", True)
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _dynamic_tool_bootstrap_entries(
    config: Any | None,
    *,
    tool_bootstrap_entries: tuple[_ToolBootstrapEntry, ...] | None = None,
) -> tuple[_ToolBootstrapEntry, ...]:
    runtime_entries = list(tool_bootstrap_entries or _TOOL_BOOTSTRAP_ENTRIES)
    if getattr(config, "mcp_servers", None):
        runtime_entries.append(_MCP_TOOL_BOOTSTRAP_ENTRY)
    return tuple(runtime_entries)


def _prepare_tool_register_state(
    *,
    entry: _ToolBootstrapEntry,
    config: Any | None,
) -> Any | None:
    if entry.module_name != "openminion.tools.mcp":
        return None
    if config is None or not getattr(config, "mcp_servers", None):
        return None

    from openminion.tools.mcp.manager import MCPFleetManager
    from openminion.tools.mcp.interfaces import MCPToolRegistrationState

    fleet_manager = MCPFleetManager.from_runtime_config(config)
    if not fleet_manager.has_servers():
        return None
    if bool(getattr(config, "mcp_deferred_discovery_enabled", False)):
        return MCPToolRegistrationState(
            manager=fleet_manager,
            discovered_tools=(),
            discovered_prompts=(),
            discovered_resources=(),
            discovered_resource_templates=(),
            client_capability_state=fleet_manager.client_capability_state,
        )
    discovered_tools = tuple(fleet_manager.discover_tools(parallel=True))
    discovered_prompts = tuple(fleet_manager.discover_prompts(parallel=True))
    discovered_resources = tuple(fleet_manager.discover_resources(parallel=True))
    discovered_resource_templates = tuple(
        fleet_manager.discover_resource_templates(parallel=True)
    )
    return MCPToolRegistrationState(
        manager=fleet_manager,
        discovered_tools=discovered_tools,
        discovered_prompts=discovered_prompts,
        discovered_resources=discovered_resources,
        discovered_resource_templates=discovered_resource_templates,
        client_capability_state=fleet_manager.client_capability_state,
    )


def _apply_dynamic_runtime_ownership(
    *,
    registry: Any,
    prepared_state: Any | None,
) -> None:
    """Attach runtime-owned handles after registrar-based tool registration."""
    from openminion.tools.mcp.interfaces import MCPToolRegistrationState

    if isinstance(prepared_state, MCPToolRegistrationState):
        registry.mcp_manager = prepared_state.manager
        prepared_state.manager.attach_registry(registry)


def _prepared_state_record_details(
    prepared_state: Any | None,
) -> tuple[list[str] | None, str]:
    added_runtime_tools = list(getattr(prepared_state, "added_runtime_tools", ()) or ())
    error_summary = str(getattr(prepared_state, "error_summary", "") or "").strip()
    return (added_runtime_tools or None, error_summary)


_TOOL_BOOTSTRAP_ENTRIES: tuple[_ToolBootstrapEntry, ...] = (
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.file",
        label="File",
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.code",
        label="Code",
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.exec",
        label="Exec",
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.browser",
        label="Browser",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.browser.providers.playwright",
        label="Browser Playwright",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.browser.providers.pinchtab",
        label="PinchTab",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.search",
        label="Search",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.search.providers.tavily",
        label="Tavily",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.weather",
        label="Weather",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.weather.providers.openmeteo",
        label="Weather OpenMeteo",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.weather.providers.weatherapi",
        label="Weather WeatherAPI",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.utility",
        label="Utility",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.time",
        label="Time",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.location",
        label="Location",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.host",
        label="Host",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.ip",
        label="IP",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.github",
        label="GitHub",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.fetch",
        label="Fetch",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.fetch.providers.scrapling",
        label="Fetch Scrapling",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.fetch.providers.firecrawl",
        label="Fetch Firecrawl",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.fetch.providers.tinyfish",
        label="Fetch TinyFish",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.reaction",
        label="Reactions",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.gws",
        label="GWS",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.search.providers.brave",
        label="Brave Search",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.search.providers.serpapi",
        label="SerpApi Search",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.search.providers.firecrawl",
        label="Firecrawl Search",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.search.providers.serper",
        label="Serper Search",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.search.providers.tinyfish",
        label="TinyFish Search",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.tool_catalog",
        label="Tool Catalog",
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.skill",
        label="Skill",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.memory",
        label="Memory",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.tool_authoring",
        label="Tool Authoring",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.task",
        label="Task",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.todo",
        label="Plan",
        required=False,
    ),
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.git",
        label="Git",
        required=False,
    ),
    # agent delegation family
    _ToolBootstrapEntry(
        kind="tool",
        module_name="openminion.tools.agent",
        label="Agent Delegation",
        required=False,
    ),
)
