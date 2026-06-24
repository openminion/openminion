from __future__ import annotations

import pytest

from openminion.base.config.base import ConfigError
from openminion.base.config.mcp import (
    MCPApprovalConfig,
    MCPServerConfig,
    MCPToolRiskOverrideConfig,
    coerce_mcp_server_configs,
)
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.policy import Policy
from openminion.tools.mcp.manager import _resolve_mcp_tool_posture
from openminion.tools.mcp.plugin import build_mcp_tool_spec
from openminion.tools.mcp.schemas import MCPListedTool, MCPToolPosture


class _StubMCPManager:
    def __init__(self, server: MCPServerConfig | None = None) -> None:
        self._server = server or _server()
        self.calls: list[dict] = []

    def server_config(self, server_name: str) -> MCPServerConfig | None:
        if server_name == self._server.name:
            return self._server
        return None

    def call_tool(
        self,
        *,
        server_name: str,
        remote_name: str,
        arguments: dict,
    ) -> dict:
        self.calls.append(
            {
                "server_name": server_name,
                "remote_name": remote_name,
                "arguments": dict(arguments),
            }
        )
        return {
            "content": [{"type": "text", "text": "ok"}],
            "mcp_server": server_name,
            "mcp_remote_tool_name": remote_name,
            "arguments": arguments,
        }


def _server(
    *,
    tool_risk_overrides: list[MCPToolRiskOverrideConfig] | None = None,
    approval: MCPApprovalConfig | None = None,
) -> MCPServerConfig:
    return MCPServerConfig(
        name="Fixture",
        transport="stdio",
        command=["python", "-m", "fixture"],
        tool_risk_overrides=tool_risk_overrides or [],
        approval=approval or MCPApprovalConfig(),
    )


def _tool(posture: MCPToolPosture | None = None) -> MCPListedTool:
    return MCPListedTool(
        server_name="fixture",
        remote_name="delete-all",
        description="fixture",
        input_schema={"type": "object", "properties": {}},
        posture=posture or MCPToolPosture(),
    )


def test_mcp_unknown_remote_tool_defaults_to_conservative_write_safe() -> None:
    spec = build_mcp_tool_spec(manager=_StubMCPManager(), tool=_tool())

    assert spec.min_scope == "WRITE_SAFE"
    assert spec.dangerous is False
    assert spec.idempotent is False


def test_mcp_read_only_and_idempotent_annotations_flow_to_tool_spec() -> None:
    posture = _resolve_mcp_tool_posture(
        server=_server(),
        remote_name="read-status",
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    spec = build_mcp_tool_spec(
        manager=_StubMCPManager(),
        tool=_tool(posture=posture),
    )

    assert spec.min_scope == "READ_ONLY"
    assert spec.dangerous is False
    assert spec.idempotent is True


def test_mcp_open_world_hint_prevents_read_only_posture() -> None:
    posture = _resolve_mcp_tool_posture(
        server=_server(),
        remote_name="search-web",
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )

    assert posture.min_scope == "WRITE_SAFE"
    assert posture.dangerous is False
    assert posture.idempotent is True


def test_mcp_destructive_annotation_requires_power_user_policy_scope() -> None:
    posture = _resolve_mcp_tool_posture(
        server=_server(),
        remote_name="delete-all",
        annotations={"destructiveHint": True, "idempotentHint": True},
    )
    spec = build_mcp_tool_spec(
        manager=_StubMCPManager(),
        tool=_tool(posture=posture),
    )

    assert spec.min_scope == "POWER_USER"
    assert spec.dangerous is True
    assert spec.idempotent is False

    policy = Policy(raw={"scope": "WRITE_SAFE"})
    with pytest.raises(ToolRuntimeError, match="requires scope 'POWER_USER'"):
        policy.ensure_scope_allowed("WRITE_SAFE", spec.min_scope, spec.name)


def test_mcp_operator_risk_override_matches_runtime_tool_name_pattern() -> None:
    posture = _resolve_mcp_tool_posture(
        server=_server(
            tool_risk_overrides=[
                MCPToolRiskOverrideConfig(
                    pattern="mcp.fixture.delete_*",
                    min_scope="POWER_USER",
                    dangerous=True,
                    idempotent=False,
                )
            ]
        ),
        remote_name="delete-all",
        annotations={"readOnlyHint": True},
    )

    assert posture.min_scope == "POWER_USER"
    assert posture.dangerous is True
    assert posture.idempotent is False


def test_mcp_tool_risk_overrides_parse_from_runtime_config_payload() -> None:
    servers = coerce_mcp_server_configs(
        [
            {
                "name": "Fixture",
                "transport": "stdio",
                "command": ["python", "-m", "fixture"],
                "tool_risk_overrides": [
                    {
                        "pattern": "delete-*",
                        "min_scope": "POWER_USER",
                        "dangerous": True,
                        "idempotent": False,
                    }
                ],
            }
        ]
    )

    assert servers[0].tool_risk_overrides == [
        MCPToolRiskOverrideConfig(
            pattern="delete-*",
            min_scope="POWER_USER",
            dangerous=True,
            idempotent=False,
        )
    ]


def test_mcp_tool_risk_override_rejects_invalid_scope() -> None:
    with pytest.raises(ConfigError, match="tool_risk_overrides"):
        MCPToolRiskOverrideConfig(
            pattern="*",
            min_scope="ROOT",
        )


def test_mcp_approval_required_denial_returns_structured_tool_result() -> None:
    manager = _StubMCPManager(_server(approval=MCPApprovalConfig(mode="always")))
    spec = build_mcp_tool_spec(manager=manager, tool=_tool())
    registry = ToolRegistry()
    registry.add(spec)

    batch = registry.execute_calls(
        [
            ProviderToolCall(
                name=spec.name,
                arguments={},
                source="native",
            )
        ],
        context=ToolExecutionContext(
            channel="console",
            target="unit-test",
            session_id="session-mcp-approval",
            metadata={"tool_call_origin": "model"},
        ),
    )

    result = batch.results[0]
    assert result.ok is False
    assert result.state == "denied"
    assert result.data["error_code"] == "CONFIRM_REQUIRED"
    assert result.data["details"]["approval_required"] is True
    assert result.data["details"]["requires_confirm"] is True
    assert result.data["details"]["mcp_server"] == "fixture"
    assert manager.calls == []


def test_mcp_approval_grant_allows_remote_call() -> None:
    manager = _StubMCPManager(
        _server(approval=MCPApprovalConfig(mode="matching", tool_patterns=["delete-*"]))
    )
    spec = build_mcp_tool_spec(manager=manager, tool=_tool())
    registry = ToolRegistry()
    registry.add(spec)

    batch = registry.execute_calls(
        [
            ProviderToolCall(
                name=spec.name,
                arguments={},
                source="native",
            )
        ],
        context=ToolExecutionContext(
            channel="console",
            target="unit-test",
            session_id="session-mcp-approval",
            metadata={
                "tool_call_origin": "model",
                "mcp_approval": "approved",
                "mcp_approval_tool": spec.name,
            },
        ),
    )

    result = batch.results[0]
    assert result.ok is True
    assert manager.calls == [
        {"server_name": "fixture", "remote_name": "delete-all", "arguments": {}}
    ]


def test_mcp_policy_replay_confirmation_allows_remote_call() -> None:
    manager = _StubMCPManager(_server(approval=MCPApprovalConfig(mode="always")))
    spec = build_mcp_tool_spec(manager=manager, tool=_tool())
    registry = ToolRegistry()
    registry.add(spec)

    batch = registry.execute_calls(
        [
            ProviderToolCall(
                name=spec.name,
                arguments={},
                source="native",
            )
        ],
        context=ToolExecutionContext(
            channel="console",
            target="unit-test",
            session_id="session-mcp-approval",
            metadata={
                "tool_call_origin": "model",
                "confirmation_source": "policy_replay",
                "confirmation_grant_id": "grant-1",
            },
        ),
    )

    result = batch.results[0]
    assert result.ok is True
    assert manager.calls == [
        {"server_name": "fixture", "remote_name": "delete-all", "arguments": {}}
    ]


def test_mcp_approval_config_parses_from_runtime_payload() -> None:
    servers = coerce_mcp_server_configs(
        [
            {
                "name": "Fixture",
                "transport": "stdio",
                "command": ["python", "-m", "fixture"],
                "approval": {
                    "mode": "matching",
                    "tool_patterns": ["mcp.fixture.delete_*"],
                    "risk_levels": ["high"],
                },
            }
        ]
    )

    assert servers[0].approval == MCPApprovalConfig(
        mode="matching",
        tool_patterns=["mcp.fixture.delete_*"],
        risk_levels=["high"],
    )
