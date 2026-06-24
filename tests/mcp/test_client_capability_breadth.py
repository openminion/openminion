from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.tools.mcp.interfaces import (
    DefaultDeclineElicitationHandler,
    MCPClientCapabilityState,
)
from openminion.tools.mcp.manager import MCPFleetManager, MCPProtocolError
from openminion.tools.mcp.schemas import MCPRoot, MCPSamplingResult


FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
)


class _FixtureSamplingHandler:
    def sample(self, *, server_name: str, request) -> MCPSamplingResult:
        assert server_name == "fixture"
        assert request.max_tokens == 32
        return MCPSamplingResult(
            role="assistant",
            content={"type": "text", "text": "hello from nested sampling"},
            model="fixture-sampler",
            stop_reason="endTurn",
        )


def _runtime_config(
    *,
    enable_client_request_tools: bool = False,
    required_capabilities: tuple[str, ...] = (),
) -> RuntimeConfig:
    env: dict[str, str] = {}
    if enable_client_request_tools:
        env["MOCK_MCP_ENABLE_CLIENT_REQUEST_TOOLS"] = "1"
    if required_capabilities:
        env["MOCK_MCP_REQUIRE_CLIENT_CAPABILITIES"] = ",".join(required_capabilities)
    return RuntimeConfig(
        mcp_servers=[
            MCPServerConfig(
                name="Fixture",
                transport="stdio",
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                env=env,
                request_timeout_seconds=5.0,
                startup_timeout_seconds=5.0,
            )
        ]
    )


def _close_bootstrap(bootstrap) -> None:
    manager = getattr(bootstrap, "mcp_manager", None)
    if manager is not None:
        manager.close()


def test_default_client_capabilities_declare_roots_and_declining_elicitation() -> None:
    config = _runtime_config(
        enable_client_request_tools=True,
        required_capabilities=("roots", "elicitation"),
    )
    bootstrap = build_runtime_bootstrap(config=config, strict=True)
    try:
        manager = bootstrap.mcp_manager
        assert manager is not None
        declared = manager.client_capability_state.declared_capabilities()
        assert "roots" in declared
        assert declared["elicitation"] == {"form": {}}
        assert "sampling" not in declared

        batch = bootstrap.registry.execute_calls(
            [
                ProviderToolCall(
                    name="mcp.fixture.request_roots",
                    arguments={},
                    source="native",
                ),
                ProviderToolCall(
                    name="mcp.fixture.request_elicitation",
                    arguments={},
                    source="native",
                ),
            ],
            context=ToolExecutionContext(
                channel="console",
                target="unit-test",
                session_id="session-mcp-client-breadth",
                metadata={"tool_call_origin": "model"},
            ),
        )
        assert len(batch.results) == 2
        roots_result = batch.results[0]
        elicitation_result = batch.results[1]
        assert roots_result.ok is True
        assert "roots:" in roots_result.content
        assert elicitation_result.ok is True
        assert elicitation_result.content.startswith("elicitation: decline")
    finally:
        _close_bootstrap(bootstrap)


def test_sampling_capability_is_not_declared_without_handler() -> None:
    config = _runtime_config(required_capabilities=("sampling",))
    manager = MCPFleetManager.from_runtime_config(config)
    try:
        with pytest.raises(
            MCPProtocolError, match="missing client capabilities: sampling"
        ):
            manager.discover_tools()
    finally:
        manager.close()


def test_sampling_capability_can_be_declared_and_used_when_handler_is_present() -> None:
    config = _runtime_config(
        enable_client_request_tools=True,
        required_capabilities=("roots", "elicitation", "sampling"),
    )
    server = config.mcp_servers[0]
    manager = MCPFleetManager(
        servers=[server],
        client_capability_state=MCPClientCapabilityState(
            roots=(
                MCPRoot(
                    uri=Path.cwd().resolve(strict=False).as_uri(),
                    name=Path.cwd().resolve(strict=False).name or "workspace",
                ),
            ),
            sampling_handler=_FixtureSamplingHandler(),
            elicitation_handler=DefaultDeclineElicitationHandler(),
        ),
    )
    try:
        declared = manager.client_capability_state.declared_capabilities()
        assert declared["sampling"] == {}

        discovered_tools = manager.discover_tools()
        assert any(tool.remote_name == "request-sampling" for tool in discovered_tools)

        result = manager.call_tool(
            server_name="fixture",
            remote_name="request-sampling",
            arguments={},
        )
        assert result["ok"] is True
        assert result["content"] == "sampling: hello from nested sampling"
    finally:
        manager.close()
