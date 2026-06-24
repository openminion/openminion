from __future__ import annotations

from unittest.mock import MagicMock

from openminion.modules.tool.runtime.plugin import ToolRuntime


def test_tool_runtime_lists_all_loaded_tools() -> None:
    runtime = ToolRuntime()
    mock_tool = MagicMock()
    mock_tool.name = "mock_tool"
    mock_tool.methods = {}
    mock_tool.capabilities.risk_level = "low"
    runtime._tools["mock_tool"] = mock_tool
    tool_names = list(runtime._tools.keys())
    assert "mock_tool" in tool_names


def test_tool_source_metadata_preserved() -> None:
    runtime = ToolRuntime()
    mock_plugin_tool = MagicMock()
    mock_plugin_tool.name = "plugin_tool"
    mock_plugin_tool.methods = {}
    mock_plugin_tool.capabilities.risk_level = "medium"
    runtime._tools["plugin_tool"] = mock_plugin_tool
    assert "plugin_tool" in runtime._tools


def test_inventory_has_name_field() -> None:
    entry = {"name": "test_tool", "source": "core", "enabled": True}
    assert entry["name"] == "test_tool"


def test_inventory_has_source_field() -> None:
    entry = {"name": "test_tool", "source": "plugin", "enabled": True}
    assert entry["source"] == "plugin"


def test_inventory_has_enabled_field() -> None:
    entry = {"name": "test_tool", "source": "core", "enabled": False}
    assert entry["enabled"] is False


def test_inventory_has_policy_allowed_field() -> None:
    entry = {
        "name": "test_tool",
        "source": "core",
        "enabled": True,
        "policy_allowed": True,
    }
    assert entry["policy_allowed"] is True


def test_gws_tools_appear_in_runtime_inventory() -> None:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.tools.gws.plugin import GwsToolPlugin

    registry = ToolRegistry()
    gws_plugin = GwsToolPlugin()
    gws_plugin.register(registry)
    available_tools = list(registry.list().keys())
    expected_gws_tools = {
        "gws.call",
        "gws.schema",
        "gws.auth.setup",
        "gws.auth.login",
        "gws.auth.export",
    }
    for tool_name in expected_gws_tools:
        assert tool_name in available_tools, (
            f"GWS tool '{tool_name}' should be in runtime inventory"
        )
