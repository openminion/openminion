from __future__ import annotations

from collections import deque
from typing import Any

from openminion.base.config.mcp import MCPServerConfig
from openminion.tools.mcp.constants import (
    MCP_INITIALIZE_METHOD,
    MCP_SERVER_DISCOVER_METHOD,
    MCP_SUBSCRIPTIONS_LISTEN_METHOD,
    MCP_TASKS_CANCEL_METHOD,
    MCP_TASKS_GET_METHOD,
    MCP_TOOLS_CALL_METHOD,
    MCP_TOOLS_LIST_METHOD,
)
from openminion.tools.mcp.contracts import MCP_MODERN_PROTOCOL_VERSION
from openminion.tools.mcp.interfaces import MCPClientCapabilityState
from openminion.tools.mcp.schemas import MCPRoot
from openminion.tools.mcp.session import MCPServerSession
from openminion.tools.mcp.transport import MCPProtocolError


class _ModernTransport:
    def __init__(self, responses: dict[str, list[dict[str, Any]]]) -> None:
        self.responses = {method: deque(items) for method, items in responses.items()}
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.notifications: list[tuple[str, dict[str, Any]]] = []
        self.running = False

    def start(self) -> None:
        self.running = True

    def is_running(self) -> bool:
        return self.running

    def request(
        self,
        *,
        method: str,
        params: dict[str, Any],
        timeout_seconds: float,
        server_request_handler: Any | None = None,
    ) -> dict[str, Any]:
        del timeout_seconds, server_request_handler
        self.requests.append((method, dict(params)))
        if method == MCP_INITIALIZE_METHOD:
            raise MCPProtocolError("method not found")
        return dict(self.responses[method].popleft())

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.notifications.append((method, dict(params)))

    def close(self) -> None:
        self.running = False


def _session(
    responses: dict[str, list[dict[str, Any]]],
    *,
    capabilities: MCPClientCapabilityState | None = None,
) -> tuple[MCPServerSession, _ModernTransport]:
    session = MCPServerSession(
        MCPServerConfig(
            name="Modern",
            transport="stdio",
            command=["modern-fixture"],
            startup_timeout_seconds=1.0,
            request_timeout_seconds=1.0,
        ),
        client_capability_state=capabilities,
    )
    transport = _ModernTransport(
        {
            MCP_SERVER_DISCOVER_METHOD: [
                {"supportedVersions": [MCP_MODERN_PROTOCOL_VERSION]}
            ],
            **responses,
        }
    )
    session._transport = transport
    return session, transport


def test_modern_discovery_replaces_initialize_session_handshake() -> None:
    session, transport = _session(
        {
            MCP_TOOLS_LIST_METHOD: [
                {
                    "resultType": "complete",
                    "ttlMs": 1000,
                    "cacheScope": "private",
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo text.",
                            "inputSchema": {"type": "object"},
                        }
                    ],
                }
            ]
        }
    )

    assert [tool.remote_name for tool in session.list_tools()] == ["echo"]
    assert [tool.remote_name for tool in session.list_tools()] == ["echo"]
    assert session.negotiated_protocol_version == MCP_MODERN_PROTOCOL_VERSION
    assert transport.notifications == []
    discover_meta = transport.requests[1][1]["_meta"]
    assert (
        discover_meta["io.modelcontextprotocol/protocolVersion"]
        == MCP_MODERN_PROTOCOL_VERSION
    )
    assert transport.requests[2][1]["_meta"] == discover_meta
    assert (
        sum(method == MCP_TOOLS_LIST_METHOD for method, _params in transport.requests)
        == 1
    )


def test_modern_input_required_result_is_fulfilled_and_retried() -> None:
    session, transport = _session(
        {
            MCP_TOOLS_CALL_METHOD: [
                {
                    "resultType": "input_required",
                    "requestState": "state-1",
                    "inputRequests": {"roots": {"method": "roots/list", "params": {}}},
                },
                {
                    "resultType": "complete",
                    "content": [{"type": "text", "text": "ready"}],
                    "isError": False,
                },
            ]
        },
        capabilities=MCPClientCapabilityState(
            roots=(MCPRoot(uri="file:///workspace", name="workspace"),)
        ),
    )

    result = session.call_tool(remote_name="needs-root", arguments={})

    assert result["content"] == "ready"
    call_params = [
        params
        for method, params in transport.requests
        if method == MCP_TOOLS_CALL_METHOD
    ]
    assert call_params[1]["requestState"] == "state-1"
    assert call_params[1]["inputResponses"]["roots"] == {
        "roots": [{"uri": "file:///workspace", "name": "workspace"}]
    }


def test_modern_task_result_is_polled_to_completion() -> None:
    session, transport = _session(
        {
            MCP_TOOLS_CALL_METHOD: [
                {"resultType": "task", "taskId": "task-1", "status": "working"}
            ],
            MCP_TASKS_GET_METHOD: [
                {
                    "taskId": "task-1",
                    "status": "completed",
                    "result": {
                        "resultType": "complete",
                        "content": [{"type": "text", "text": "finished"}],
                        "isError": False,
                    },
                }
            ],
        }
    )

    result = session.call_tool(remote_name="slow", arguments={})

    assert result["content"] == "finished"
    assert [method for method, _params in transport.requests][
        -1
    ] == MCP_TASKS_GET_METHOD


def test_modern_task_cancel_and_subscription_listen_are_explicit_methods() -> None:
    session, transport = _session(
        {
            MCP_TASKS_CANCEL_METHOD: [{"resultType": "complete", "cancelled": True}],
            MCP_SUBSCRIPTIONS_LISTEN_METHOD: [
                {"resultType": "complete", "accepted": 1}
            ],
        }
    )

    assert session.cancel_task("task-2")["cancelled"] is True
    assert session.listen([{"method": "tools/list_changed"}])["accepted"] == 1
    methods = [method for method, _params in transport.requests]
    assert MCP_TASKS_CANCEL_METHOD in methods
    assert MCP_SUBSCRIPTIONS_LISTEN_METHOD in methods
