from __future__ import annotations

import sys
from pathlib import Path

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.modules.llm.providers.base import ProviderRequest, ProviderResponse
from openminion.tools.mcp.manager import MCPFleetManager


FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
)


def _runtime_config(*, sampling_mode: str) -> RuntimeConfig:
    return RuntimeConfig(
        mcp_sampling_mode=sampling_mode,
        mcp_servers=[
            MCPServerConfig(
                name="Fixture",
                transport="stdio",
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                env={
                    "MOCK_MCP_ENABLE_CLIENT_REQUEST_TOOLS": "1",
                    "MOCK_MCP_REQUIRE_CLIENT_CAPABILITIES": "sampling",
                },
                request_timeout_seconds=5.0,
                startup_timeout_seconds=5.0,
            )
        ],
    )


def test_sampling_bridge_denies_when_policy_mode_is_deny() -> None:
    manager = MCPFleetManager.from_runtime_config(_runtime_config(sampling_mode="deny"))
    try:
        declared = manager.client_capability_state.declared_capabilities()
        assert declared["sampling"] == {}
        assert any(
            tool.remote_name == "request-sampling" for tool in manager.discover_tools()
        )

        result = manager.call_tool(
            server_name="fixture",
            remote_name="request-sampling",
            arguments={},
        )

        assert result["ok"] is True
        assert result["content"].startswith("sampling: Sampling denied")
        events = manager.mcp_sampling_events()
        assert events[-1]["allowed"] is False
        assert events[-1]["stop_reason"] == "denied"
    finally:
        manager.close()


def test_sampling_bridge_invokes_bound_openminion_provider_executor() -> None:
    captured: list[ProviderRequest] = []

    def _executor(request: ProviderRequest) -> ProviderResponse:
        captured.append(request)
        return ProviderResponse(
            text="hello from real provider path",
            model="fake-provider",
            finish_reason="endTurn",
        )

    manager = MCPFleetManager.from_runtime_config(
        _runtime_config(sampling_mode="allow")
    )
    manager.bind_sampling_executor(_executor)
    try:
        assert any(
            tool.remote_name == "request-sampling" for tool in manager.discover_tools()
        )

        result = manager.call_tool(
            server_name="fixture",
            remote_name="request-sampling",
            arguments={},
        )

        assert result["ok"] is True
        assert result["content"] == "sampling: hello from real provider path"
        assert captured
        assert captured[0].metadata["origin"] == "mcp.sampling"
        assert captured[0].metadata["mcp_server"] == "fixture"
        assert "Say hello from sampling." in captured[0].user_message
        events = manager.mcp_sampling_events()
        assert events[-1]["allowed"] is True
        assert events[-1]["model"] == "fake-provider"
    finally:
        manager.close()
