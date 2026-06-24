from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.tools.mcp.manager import MCPFleetManager


FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
)


def _runtime_config(
    *, env: dict[str, str] | None = None, command: list[str] | None = None
) -> RuntimeConfig:
    return RuntimeConfig(
        mcp_servers=[
            MCPServerConfig(
                name="Fixture",
                transport="stdio",
                command=command or [sys.executable, str(FIXTURE_SERVER_PATH)],
                env=dict(env or {}),
                request_timeout_seconds=10.0,
                startup_timeout_seconds=10.0,
            )
        ]
    )


def test_stdio_transport_supports_ndjson_fixture_by_default() -> None:
    manager = MCPFleetManager.from_runtime_config(_runtime_config())
    try:
        discovered = manager.discover_tools()
        assert {tool.remote_name for tool in discovered} >= {"echo-text", "add-numbers"}

        result = manager.call_tool(
            server_name="fixture",
            remote_name="echo-text",
            arguments={"text": "hello ndjson"},
        )
        assert result["ok"] is True
        assert result["content"] == "echo: hello ndjson"
    finally:
        manager.close()


def test_stdio_transport_supports_legacy_lsp_fixture_fallback() -> None:
    manager = MCPFleetManager.from_runtime_config(
        _runtime_config(env={"MOCK_MCP_LSP_FRAMING": "1"})
    )
    try:
        discovered = manager.discover_tools()
        assert {tool.remote_name for tool in discovered} >= {"echo-text", "add-numbers"}

        result = manager.call_tool(
            server_name="fixture",
            remote_name="echo-text",
            arguments={"text": "hello lsp"},
        )
        assert result["ok"] is True
        assert result["content"] == "echo: hello lsp"
    finally:
        manager.close()


@pytest.mark.skipif(
    shutil.which("npx") is None,
    reason="npx is required for official MCP stdio interop smoke",
)
def test_stdio_transport_smoke_against_official_server_everything() -> None:
    manager = MCPFleetManager.from_runtime_config(
        _runtime_config(
            command=["npx", "-y", "@modelcontextprotocol/server-everything"],
        )
    )
    try:
        discovered = manager.discover_tools()
        assert discovered, "official MCP server returned no tools"

        target_name = ""
        arguments: dict[str, object] = {}
        for tool in discovered:
            properties = dict(tool.input_schema.get("properties", {}) or {})
            required = {
                str(item).strip()
                for item in list(tool.input_schema.get("required", []) or [])
                if str(item).strip()
            }
            if tool.remote_name in {"echo", "echo-text", "echo_text"}:
                if "text" in properties:
                    target_name = tool.remote_name
                    arguments = {"text": "hello everything"}
                    break
                if "message" in properties:
                    target_name = tool.remote_name
                    arguments = {"message": "hello everything"}
                    break
            if not required:
                target_name = tool.remote_name
                arguments = {}
                break
        if not target_name:
            pytest.skip(
                "official MCP server did not expose a known smoke-callable tool"
            )

        result = manager.call_tool(
            server_name="fixture",
            remote_name=target_name,
            arguments=arguments,
        )
        assert result["ok"] is True
    finally:
        manager.close()
