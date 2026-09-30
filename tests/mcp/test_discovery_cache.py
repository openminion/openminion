from __future__ import annotations

import sys
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.tools.mcp.manager import MCPFleetManager


FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
)


def _runtime_config(
    *,
    cache_ttl_seconds: float = 0.0,
) -> RuntimeConfig:
    return RuntimeConfig(
        mcp_discovery_cache_ttl_seconds=cache_ttl_seconds,
        mcp_servers=[
            MCPServerConfig(
                name="Fixture",
                transport="stdio",
                trusted=True,
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                request_timeout_seconds=5.0,
                startup_timeout_seconds=5.0,
            )
        ],
    )


def test_discovery_cache_serves_fresh_tool_snapshot_without_second_server_call() -> (
    None
):
    manager = MCPFleetManager.from_runtime_config(
        _runtime_config(cache_ttl_seconds=60.0)
    )
    try:
        first = manager.discover_tools()
        assert any(tool.remote_name == "echo-text" for tool in first)

        session = manager._sessions["fixture"]

        def _fail_second_call():
            raise AssertionError("cache miss")

        session.list_tools = _fail_second_call
        second = manager.discover_tools()

        assert [tool.remote_name for tool in second] == [
            tool.remote_name for tool in first
        ]
        assert manager.discovery_cache_snapshot()["tools"]["item_count"] == len(first)
    finally:
        manager.close()


def test_discovery_cache_can_be_invalidated_explicitly() -> None:
    manager = MCPFleetManager.from_runtime_config(
        _runtime_config(cache_ttl_seconds=60.0)
    )
    try:
        assert manager.discover_tools()
        manager.invalidate_discovery_cache("tools")
        session = manager._sessions["fixture"]
        session.list_tools = lambda: []

        assert manager.discover_tools() == []
    finally:
        manager.close()


def test_legacy_deferred_flag_no_longer_suppresses_mcp_registration() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "runtime": {
                "mcp_deferred_discovery_enabled": True,
                "mcp_servers": [
                    {
                        "name": "Fixture",
                        "transport": "stdio",
                        "trusted": True,
                        "command": [sys.executable, str(FIXTURE_SERVER_PATH)],
                    }
                ],
            }
        }
    ).runtime
    bootstrap = build_runtime_bootstrap(config=config, strict=True)
    try:
        assert bootstrap.mcp_manager is not None
        runtime_names = set(bootstrap.registry.list().keys())
        assert "mcp.fixture.echo_text" in runtime_names
        assert not hasattr(config, "mcp_deferred_discovery_enabled")
    finally:
        bootstrap.mcp_manager.close()
