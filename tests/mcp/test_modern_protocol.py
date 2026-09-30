from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Any

import pytest

from openminion.base.config.mcp import MCPServerConfig
from openminion.tools.mcp.constants import (
    MCP_INITIALIZE_METHOD,
    MCP_PROMPTS_LIST_METHOD,
    MCP_RESOURCES_LIST_METHOD,
    MCP_RESOURCES_READ_METHOD,
    MCP_RESOURCES_TEMPLATES_LIST_METHOD,
    MCP_SERVER_DISCOVER_METHOD,
    MCP_SUBSCRIPTIONS_LISTEN_METHOD,
    MCP_TASKS_CANCEL_METHOD,
    MCP_TASKS_GET_METHOD,
    MCP_TASKS_UPDATE_METHOD,
    MCP_TOOLS_CALL_METHOD,
    MCP_TOOLS_LIST_METHOD,
)
from openminion.tools.mcp.contracts import MCP_MODERN_PROTOCOL_VERSION
from openminion.tools.mcp.interfaces import MCPClientCapabilityState
from openminion.tools.mcp.modern import (
    MCPModernFlowError,
    MCPModernResponseCache,
    resolve_modern_result,
)
from openminion.tools.mcp.schemas import MCPRoot
from openminion.tools.mcp.session import MCPServerSession
from openminion.tools.mcp.transport import MCPProtocolError, StdioMCPTransport


class _ModernTransport:
    def __init__(self, responses: dict[str, list[dict[str, Any]]]) -> None:
        self.responses = {method: deque(items) for method, items in responses.items()}
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.notifications: list[tuple[str, dict[str, Any]]] = []
        self.cancelled_requests: list[int] = []
        self.running = False

    @property
    def authorization_identity(self) -> str:
        return ""

    def stderr_tail(self, *, limit: int = 4096) -> str:
        del limit
        return ""

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

    def cancel_request(self, request_id: int) -> None:
        self.cancelled_requests.append(request_id)

    def close(self) -> None:
        self.running = False


class _LegacyFallbackTransport(_ModernTransport):
    def __init__(self, discovery: dict[str, Any] | None = None) -> None:
        super().__init__({MCP_SERVER_DISCOVER_METHOD: [discovery or {}]})
        self.start_count = 0

    def start(self) -> None:
        self.start_count += 1
        self.running = True

    def request(self, **kwargs):
        method = kwargs["method"]
        if method == MCP_INITIALIZE_METHOD:
            self.requests.append((method, dict(kwargs["params"])))
            return {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
            }
        if method == MCP_TOOLS_LIST_METHOD:
            self.requests.append((method, dict(kwargs["params"])))
            return {"tools": []}
        return super().request(**kwargs)


def _session(
    responses: dict[str, list[dict[str, Any]]],
    *,
    capabilities: MCPClientCapabilityState | None = None,
    server_capabilities: dict[str, Any] | None = None,
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
                {
                    "supportedVersions": [MCP_MODERN_PROTOCOL_VERSION],
                    "capabilities": server_capabilities or {"tools": {}},
                    "instructions": "Diagnostic guidance only.",
                    "ttlMs": 0,
                    "cacheScope": "private",
                    "_meta": {
                        "io.modelcontextprotocol/serverInfo": {
                            "name": "modern-fixture",
                            "version": "1.0.0",
                        }
                    },
                }
            ],
            **responses,
        }
    )
    session._transport = transport
    return session, transport


def _task(status: str, **values: Any) -> dict[str, Any]:
    return {
        "taskId": "task-1",
        "status": status,
        "createdAt": "2026-09-30T00:00:00Z",
        "lastUpdatedAt": "2026-09-30T00:00:01Z",
        "ttlMs": 60_000,
        **values,
    }


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
                            "title": "Echo",
                            "description": "Echo text.",
                            "inputSchema": {"type": "object"},
                            "icons": [{"src": "https://example.test/echo.png"}],
                            "_meta": {"vendor": "fixture"},
                            "execution": {"taskSupport": "optional"},
                        }
                    ],
                }
            ]
        }
    )

    tools = session.list_tools()
    assert [tool.remote_name for tool in tools] == ["echo"]
    assert tools[0].title == "Echo"
    assert tools[0].icons == ({"src": "https://example.test/echo.png"},)
    assert tools[0].metadata == {"vendor": "fixture"}
    assert tools[0].task_support == "optional"
    assert [tool.remote_name for tool in session.list_tools()] == ["echo"]
    assert session.negotiated_protocol_version == MCP_MODERN_PROTOCOL_VERSION
    assert session.server_info == {"name": "modern-fixture", "version": "1.0.0"}
    assert session.server_capabilities == {"tools": {}}
    assert session.server_instructions == "Diagnostic guidance only."
    assert transport.notifications == []


def test_unadvertised_discovery_primitives_make_no_wire_calls() -> None:
    session, transport = _session(
        {
            MCP_TOOLS_LIST_METHOD: [
                {
                    "resultType": "complete",
                    "ttlMs": 0,
                    "cacheScope": "private",
                    "tools": [],
                }
            ]
        },
        server_capabilities={"tools": {}},
    )

    assert session.list_tools() == []
    request_count = len(transport.requests)
    assert session.list_prompts() == []
    assert session.list_resources() == []
    assert session.list_resource_templates() == []
    assert len(transport.requests) == request_count
    discover_meta = transport.requests[0][1]["_meta"]
    assert (
        discover_meta["io.modelcontextprotocol/protocolVersion"]
        == MCP_MODERN_PROTOCOL_VERSION
    )
    assert transport.requests[1][1]["_meta"] == discover_meta
    assert (
        sum(method == MCP_TOOLS_LIST_METHOD for method, _params in transport.requests)
        == 1
    )


def test_stdio_malformed_discovery_success_does_not_downgrade() -> None:
    session = MCPServerSession(
        MCPServerConfig(name="Legacy", command=["legacy-fixture"])
    )
    transport = _LegacyFallbackTransport()
    session._transport = transport  # noqa: SLF001

    with pytest.raises(MCPProtocolError) as excinfo:
        session.list_tools()

    assert excinfo.value.reason_code == "mcp_modern_protocol_unavailable"
    assert transport.start_count == 1
    assert [method for method, _params in transport.requests] == [
        MCP_SERVER_DISCOVER_METHOD
    ]


@pytest.mark.parametrize("code", [-32601, -32022])
def test_stdio_downgrades_only_for_unsupported_modern_errors(code: int) -> None:
    class _UnsupportedDiscoveryTransport(_LegacyFallbackTransport):
        def request(self, **kwargs):
            if kwargs["method"] == MCP_SERVER_DISCOVER_METHOD:
                self.requests.append((kwargs["method"], dict(kwargs["params"])))
                raise MCPProtocolError("unsupported", details={"code": code})
            return super().request(**kwargs)

    session = MCPServerSession(
        MCPServerConfig(name="Legacy", command=["legacy-fixture"])
    )
    transport = _UnsupportedDiscoveryTransport()
    session._transport = transport  # noqa: SLF001

    assert session.list_tools() == []
    assert transport.start_count == 2


def test_discovery_preserves_prompt_and_resource_metadata() -> None:
    session, _transport = _session(
        {
            MCP_PROMPTS_LIST_METHOD: [
                {
                    "resultType": "complete",
                    "ttlMs": 0,
                    "cacheScope": "private",
                    "prompts": [
                        {
                            "name": "daily",
                            "title": "Daily",
                            "icons": [{"src": "https://example.test/daily.png"}],
                            "_meta": {"vendor": "fixture"},
                        }
                    ],
                }
            ],
            MCP_RESOURCES_LIST_METHOD: [
                {
                    "resultType": "complete",
                    "ttlMs": 0,
                    "cacheScope": "private",
                    "resources": [
                        {
                            "uri": "file:///readme.md",
                            "name": "readme",
                            "title": "Readme",
                            "icons": [{"src": "https://example.test/readme.png"}],
                            "_meta": {"vendor": "fixture"},
                        }
                    ],
                }
            ],
            MCP_RESOURCES_TEMPLATES_LIST_METHOD: [
                {
                    "resultType": "complete",
                    "ttlMs": 0,
                    "cacheScope": "private",
                    "resourceTemplates": [
                        {
                            "uriTemplate": "file:///{path}",
                            "name": "file",
                            "title": "File",
                            "icons": [{"src": "https://example.test/file.png"}],
                            "_meta": {"vendor": "fixture"},
                        }
                    ],
                }
            ],
        },
        server_capabilities={"tools": {}, "prompts": {}, "resources": {}},
    )

    prompt = session.list_prompts()[0]
    resource = session.list_resources()[0]
    template = session.list_resource_templates()[0]

    assert (prompt.title, resource.title, template.title) == (
        "Daily",
        "Readme",
        "File",
    )
    assert (
        prompt.metadata
        == resource.metadata
        == template.metadata
        == {"vendor": "fixture"}
    )
    assert prompt.icons[0]["src"].endswith("daily.png")
    assert resource.icons[0]["src"].endswith("readme.png")
    assert template.icons[0]["src"].endswith("file.png")


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


def test_modern_input_required_accepts_request_state_without_requests() -> None:
    session, transport = _session(
        {
            MCP_TOOLS_CALL_METHOD: [
                {"resultType": "input_required", "requestState": "state-1"},
                {
                    "resultType": "complete",
                    "content": [{"type": "text", "text": "ready"}],
                    "isError": False,
                },
            ]
        }
    )

    result = session.call_tool(remote_name="needs-state", arguments={})

    assert result["content"] == "ready"
    call_params = [
        params
        for method, params in transport.requests
        if method == MCP_TOOLS_CALL_METHOD
    ]
    assert call_params[1]["requestState"] == "state-1"
    assert "inputResponses" not in call_params[1]


def test_modern_input_required_accepts_requests_without_request_state() -> None:
    session, transport = _session(
        {
            MCP_TOOLS_CALL_METHOD: [
                {
                    "resultType": "input_required",
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
    assert "requestState" not in call_params[1]
    assert call_params[1]["inputResponses"]["roots"] == {
        "roots": [{"uri": "file:///workspace", "name": "workspace"}]
    }


def test_modern_task_result_is_polled_to_completion() -> None:
    session, transport = _session(
        {
            MCP_TOOLS_CALL_METHOD: [{"resultType": "task", **_task("working")}],
            MCP_TASKS_GET_METHOD: [
                {
                    "resultType": "complete",
                    **_task(
                        "completed",
                        result={
                            "resultType": "complete",
                            "content": [{"type": "text", "text": "finished"}],
                            "isError": False,
                        },
                    ),
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
    assert session.listen({"toolsListChanged": True})["accepted"] == 1
    methods = [method for method, _params in transport.requests]
    assert MCP_TASKS_CANCEL_METHOD in methods
    assert MCP_SUBSCRIPTIONS_LISTEN_METHOD in methods
    listen_params = next(
        params
        for method, params in transport.requests
        if method == MCP_SUBSCRIPTIONS_LISTEN_METHOD
    )
    assert listen_params["notifications"] == {"toolsListChanged": True}


def test_modern_logging_level_starts_session_and_uses_request_metadata() -> None:
    session, transport = _session({})

    session.set_log_level("NOTICE")

    assert [method for method, _params in transport.requests] == [
        MCP_SERVER_DISCOVER_METHOD
    ]
    assert (
        session._with_client_meta({})["_meta"][  # noqa: SLF001
            "io.modelcontextprotocol/logLevel"
        ]
        == "notice"
    )


def test_modern_resource_subscription_uses_listen_and_http_cancel_is_silent() -> None:
    session, transport = _session({})
    session._server.transport = "streamable_http"  # noqa: SLF001
    session.start()

    with pytest.raises(MCPProtocolError) as exc_info:
        session.subscribe_resource(resource_uri="file:///readme")
    assert exc_info.value.reason_code == "mcp_legacy_resource_subscription_only"

    session.cancel(7)
    assert transport.notifications == []
    assert transport.cancelled_requests == [7]


def test_modern_stdio_cancel_sends_notification() -> None:
    session, transport = _session({})
    session.start()

    session.cancel(7)

    assert transport.notifications == [("notifications/cancelled", {"requestId": 7})]


def test_stdio_transport_correlates_concurrent_responses(monkeypatch) -> None:
    transport = StdioMCPTransport(MCPServerConfig(name="fixture", command=["fixture"]))
    writes: list[dict[str, Any]] = []
    both_written = threading.Event()
    responses = deque(
        [
            {"jsonrpc": "2.0", "id": 2, "result": {"value": "second"}},
            {"jsonrpc": "2.0", "id": 1, "result": {"value": "first"}},
        ]
    )

    monkeypatch.setattr(transport, "start", lambda: None)

    def write(payload: dict[str, Any]) -> None:
        writes.append(payload)
        if len(writes) == 2:
            both_written.set()

    def read(*, deadline: float) -> dict[str, Any]:
        del deadline
        assert both_written.wait(timeout=1)
        return responses.popleft()

    monkeypatch.setattr(transport, "_write_message", write)
    monkeypatch.setattr(transport, "_read_message", read)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            transport.request,
            method="first",
            params={},
            timeout_seconds=1,
        )
        second = pool.submit(
            transport.request,
            method="second",
            params={},
            timeout_seconds=1,
        )
        assert {
            first.result(timeout=2)["value"],
            second.result(timeout=2)["value"],
        } == {
            "first",
            "second",
        }


def test_modern_stdio_subscription_validates_ack_and_event_correlation(
    monkeypatch,
) -> None:
    transport = StdioMCPTransport(MCPServerConfig(name="fixture", command=["fixture"]))
    messages = deque(
        [
            {
                "jsonrpc": "2.0",
                "method": "notifications/subscriptions/acknowledged",
                "params": {
                    "notifications": {"toolsListChanged": True},
                    "_meta": {"io.modelcontextprotocol/subscriptionId": 1},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/tools/list_changed",
                "params": {"_meta": {"io.modelcontextprotocol/subscriptionId": 1}},
            },
            {"jsonrpc": "2.0", "id": 1, "result": {}},
        ]
    )
    notifications: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(transport, "start", lambda: None)
    monkeypatch.setattr(transport, "_write_message", lambda _payload: None)
    monkeypatch.setattr(
        transport, "_read_message", lambda **_kwargs: messages.popleft()
    )

    result = transport.request(
        method=MCP_SUBSCRIPTIONS_LISTEN_METHOD,
        params={
            "notifications": {"toolsListChanged": True},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": (MCP_MODERN_PROTOCOL_VERSION)
            },
        },
        timeout_seconds=1,
        server_request_handler=lambda **kwargs: notifications.append(
            (kwargs["method"], kwargs["params"])
        ),
    )

    assert result["eventCount"] == 1
    assert result["notifications"] == {"toolsListChanged": True}
    assert notifications[0][0] == "notifications/tools/list_changed"


def test_modern_stdio_subscription_accepts_requested_resource_update(
    monkeypatch,
) -> None:
    uri = "file:///readme"
    transport = StdioMCPTransport(MCPServerConfig(name="fixture", command=["fixture"]))
    messages = deque(
        [
            {
                "jsonrpc": "2.0",
                "method": "notifications/subscriptions/acknowledged",
                "params": {
                    "notifications": {"resourceSubscriptions": [uri]},
                    "_meta": {"io.modelcontextprotocol/subscriptionId": 1},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/resources/updated",
                "params": {
                    "uri": uri,
                    "_meta": {"io.modelcontextprotocol/subscriptionId": 1},
                },
            },
            {"jsonrpc": "2.0", "id": 1, "result": {}},
        ]
    )
    monkeypatch.setattr(transport, "start", lambda: None)
    monkeypatch.setattr(transport, "_write_message", lambda _payload: None)
    monkeypatch.setattr(
        transport, "_read_message", lambda **_kwargs: messages.popleft()
    )

    result = transport.request(
        method=MCP_SUBSCRIPTIONS_LISTEN_METHOD,
        params={"notifications": {"resourceSubscriptions": [uri]}},
        timeout_seconds=1,
    )

    assert result["notifications"] == {"resourceSubscriptions": [uri]}
    assert result["eventCount"] == 1


def test_modern_stdio_subscription_rejects_event_before_ack(monkeypatch) -> None:
    transport = StdioMCPTransport(MCPServerConfig(name="fixture", command=["fixture"]))
    monkeypatch.setattr(transport, "start", lambda: None)
    monkeypatch.setattr(transport, "_write_message", lambda _payload: None)
    monkeypatch.setattr(
        transport,
        "_read_message",
        lambda **_kwargs: {
            "jsonrpc": "2.0",
            "method": "notifications/tools/list_changed",
            "params": {"_meta": {"io.modelcontextprotocol/subscriptionId": 1}},
        },
    )

    with pytest.raises(MCPProtocolError) as excinfo:
        transport.request(
            method=MCP_SUBSCRIPTIONS_LISTEN_METHOD,
            params={"notifications": {"toolsListChanged": True}},
            timeout_seconds=1,
        )

    assert excinfo.value.reason_code == "mcp_subscription_protocol_error"


@pytest.mark.parametrize(
    "event",
    [
        {
            "jsonrpc": "2.0",
            "method": "notifications/tools/list_changed",
            "params": {"_meta": {"io.modelcontextprotocol/subscriptionId": 99}},
        },
        {
            "jsonrpc": "2.0",
            "method": "notifications/resources/list_changed",
            "params": {"_meta": {"io.modelcontextprotocol/subscriptionId": 1}},
        },
        {
            "jsonrpc": "2.0",
            "method": "notifications/resources/updated",
            "params": {
                "uri": "file:///other",
                "_meta": {"io.modelcontextprotocol/subscriptionId": 1},
            },
        },
    ],
)
def test_modern_stdio_subscription_rejects_uncorrelated_or_unaccepted_event(
    monkeypatch, event
) -> None:
    transport = StdioMCPTransport(MCPServerConfig(name="fixture", command=["fixture"]))
    messages = deque(
        [
            {
                "jsonrpc": "2.0",
                "method": "notifications/subscriptions/acknowledged",
                "params": {
                    "notifications": {
                        "toolsListChanged": True,
                        "resourceSubscriptions": ["file:///readme"],
                    },
                    "_meta": {"io.modelcontextprotocol/subscriptionId": 1},
                },
            },
            event,
        ]
    )
    monkeypatch.setattr(transport, "start", lambda: None)
    monkeypatch.setattr(transport, "_write_message", lambda _payload: None)
    monkeypatch.setattr(
        transport, "_read_message", lambda **_kwargs: messages.popleft()
    )

    with pytest.raises(MCPProtocolError) as excinfo:
        transport.request(
            method=MCP_SUBSCRIPTIONS_LISTEN_METHOD,
            params={
                "notifications": {
                    "toolsListChanged": True,
                    "resourceSubscriptions": ["file:///readme"],
                }
            },
            timeout_seconds=1,
        )

    assert excinfo.value.reason_code == "mcp_subscription_protocol_error"


def test_modern_stdio_rejects_server_initiated_request(monkeypatch) -> None:
    transport = StdioMCPTransport(MCPServerConfig(name="fixture", command=["fixture"]))
    writes: list[dict[str, Any]] = []
    monkeypatch.setattr(transport, "start", lambda: None)
    monkeypatch.setattr(transport, "_write_message", writes.append)
    monkeypatch.setattr(
        transport,
        "_read_message",
        lambda **_kwargs: {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "roots/list",
            "params": {},
        },
    )

    with pytest.raises(MCPProtocolError) as exc_info:
        transport.request(
            method="tools/list",
            params={
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": (
                        MCP_MODERN_PROTOCOL_VERSION
                    )
                }
            },
            timeout_seconds=1,
        )

    assert exc_info.value.reason_code == "mcp_modern_stdio_server_request"
    assert len(writes) == 1


def test_modern_response_cache_has_a_fixed_entry_bound() -> None:
    cache = MCPModernResponseCache()
    for index in range(129):
        cache.store(
            method="tools/list",
            params={"cursor": str(index)},
            result={"ttlMs": 60_000, "cacheScope": "private", "index": index},
        )

    assert cache.get(method="tools/list", params={"cursor": "0"}) is None
    assert cache.get(method="tools/list", params={"cursor": "128"}) == {
        "ttlMs": 60_000,
        "cacheScope": "private",
        "index": 128,
    }


def test_modern_response_cache_is_isolated_by_authorization_identity() -> None:
    cache = MCPModernResponseCache()
    cached = {"ttlMs": 60_000, "cacheScope": "private", "tools": ["alice"]}
    cache.store(
        method="tools/list",
        params={},
        result=cached,
        identity="alice-token-ref",
    )

    assert cache.get(method="tools/list", params={}, identity="bob-token-ref") is None
    assert (
        cache.get(method="tools/list", params={}, identity="alice-token-ref") == cached
    )


def test_modern_list_change_notification_forces_wire_refetch() -> None:
    session, transport = _session(
        {
            MCP_TOOLS_LIST_METHOD: [
                {
                    "resultType": "complete",
                    "ttlMs": 60_000,
                    "cacheScope": "private",
                    "tools": [{"name": "before", "inputSchema": {"type": "object"}}],
                },
                {
                    "resultType": "complete",
                    "ttlMs": 60_000,
                    "cacheScope": "private",
                    "tools": [{"name": "after", "inputSchema": {"type": "object"}}],
                },
            ]
        }
    )

    assert session.list_tools()[0].remote_name == "before"
    session._handle_server_notification(  # noqa: SLF001
        method="notifications/tools/list_changed", params={}
    )
    assert session.list_tools()[0].remote_name == "after"
    assert (
        sum(method == MCP_TOOLS_LIST_METHOD for method, _params in transport.requests)
        == 2
    )


def test_modern_resource_update_clears_cached_read() -> None:
    session, _transport = _session({})
    params = session._with_client_meta({"uri": "file:///readme"})  # noqa: SLF001
    session._response_cache.store(  # noqa: SLF001
        method=MCP_RESOURCES_READ_METHOD,
        params=params,
        result={"ttlMs": 60_000, "cacheScope": "private", "contents": []},
    )

    session._handle_server_notification(  # noqa: SLF001
        method="notifications/resources/updated", params={"uri": "file:///readme"}
    )

    assert (
        session._response_cache.get(  # noqa: SLF001
            method=MCP_RESOURCES_READ_METHOD, params=params
        )
        is None
    )


@pytest.mark.parametrize(
    ("result", "reason_code"),
    [
        ({"cacheScope": "private"}, "mcp_cache_ttl_invalid"),
        ({"ttlMs": 60}, "mcp_cache_scope_invalid"),
        ({"ttlMs": "60", "cacheScope": "private"}, "mcp_cache_ttl_invalid"),
        ({"ttlMs": 60, "cacheScope": "shared"}, "mcp_cache_scope_invalid"),
    ],
)
def test_modern_response_cache_rejects_invalid_metadata(
    result: dict[str, Any], reason_code: str
) -> None:
    with pytest.raises(MCPModernFlowError) as excinfo:
        MCPModernResponseCache().store(method="tools/list", params={}, result=result)

    assert excinfo.value.reason_code == reason_code


def test_modern_task_timeout_cancels_remote_task(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append((method, params))
        return {"resultType": "complete"}

    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(
        "openminion.tools.mcp.modern.time.monotonic",
        lambda: next(ticks),
    )

    with pytest.raises(MCPModernFlowError, match="did not complete before timeout"):
        resolve_modern_result(
            method=MCP_TOOLS_CALL_METHOD,
            params={},
            result={"resultType": "task", **_task("working")},
            request=request,
            fulfill=lambda _method, _params: {},
            timeout_seconds=0.1,
        )

    assert calls == [(MCP_TASKS_CANCEL_METHOD, {"taskId": "task-1"})]


def test_modern_task_get_requires_complete_result() -> None:
    with pytest.raises(MCPModernFlowError) as excinfo:
        resolve_modern_result(
            method=MCP_TOOLS_CALL_METHOD,
            params={},
            result={"resultType": "task", **_task("working")},
            request=lambda _method, _params: {
                "resultType": "task",
                **_task("working"),
            },
            fulfill=lambda _method, _params: {},
            timeout_seconds=1.0,
        )

    assert excinfo.value.reason_code == "mcp_task_result_type_invalid"


def test_modern_task_update_requires_complete_result() -> None:
    def request(method: str, _params: dict[str, Any]) -> dict[str, Any]:
        if method == MCP_TASKS_UPDATE_METHOD:
            return {"resultType": "input_required"}
        return {"resultType": "complete", **_task("working")}

    with pytest.raises(MCPModernFlowError) as excinfo:
        resolve_modern_result(
            method=MCP_TOOLS_CALL_METHOD,
            params={},
            result={
                "resultType": "task",
                **_task(
                    "input_required",
                    inputRequests={"roots": {"method": "roots/list", "params": {}}},
                ),
            },
            request=request,
            fulfill=lambda _method, _params: {"roots": []},
            timeout_seconds=1.0,
        )

    assert excinfo.value.reason_code == "mcp_task_result_type_invalid"


def test_modern_task_cancel_requires_complete_result(monkeypatch) -> None:
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(
        "openminion.tools.mcp.modern.time.monotonic",
        lambda: next(ticks),
    )

    with pytest.raises(MCPModernFlowError) as excinfo:
        resolve_modern_result(
            method=MCP_TOOLS_CALL_METHOD,
            params={},
            result={"resultType": "task", **_task("working")},
            request=lambda _method, _params: {"resultType": "task"},
            fulfill=lambda _method, _params: {},
            timeout_seconds=0.1,
        )

    assert excinfo.value.reason_code == "mcp_task_result_type_invalid"


@pytest.mark.parametrize(
    ("task", "reason_code"),
    [
        (
            {"resultType": "task", **_task("future")},
            "mcp_task_status_invalid",
        ),
        (
            {
                "resultType": "task",
                **_task("working"),
                "pollIntervalMs": "10",
            },
            "mcp_task_poll_interval_invalid",
        ),
        (
            {"resultType": "task", **_task("COMPLETED")},
            "mcp_task_status_invalid",
        ),
        (
            {"resultType": "task", **_task("working"), "taskId": 123},
            "mcp_task_id_missing",
        ),
    ],
)
def test_modern_task_rejects_invalid_state(
    task: dict[str, Any], reason_code: str
) -> None:
    with pytest.raises(MCPModernFlowError) as excinfo:
        resolve_modern_result(
            method=MCP_TOOLS_CALL_METHOD,
            params={},
            result=task,
            request=lambda _method, _params: {},
            fulfill=lambda _method, _params: {},
            timeout_seconds=1.0,
        )

    assert excinfo.value.reason_code == reason_code


def test_modern_task_rejects_changed_task_id() -> None:
    with pytest.raises(MCPModernFlowError) as excinfo:
        resolve_modern_result(
            method=MCP_TOOLS_CALL_METHOD,
            params={},
            result={"resultType": "task", **_task("working")},
            request=lambda _method, _params: {
                "resultType": "complete",
                **_task("completed"),
                "taskId": "task-2",
                "result": {"resultType": "complete"},
            },
            fulfill=lambda _method, _params: {},
            timeout_seconds=1.0,
        )

    assert excinfo.value.reason_code == "mcp_task_id_mismatch"


def test_modern_non_tool_method_rejects_task_result() -> None:
    with pytest.raises(MCPModernFlowError) as excinfo:
        resolve_modern_result(
            method=MCP_TOOLS_LIST_METHOD,
            params={},
            result={"resultType": "task", **_task("working")},
            request=lambda _method, _params: {},
            fulfill=lambda _method, _params: {},
            timeout_seconds=1.0,
        )

    assert excinfo.value.reason_code == "mcp_task_method_invalid"


def test_modern_input_required_rejects_missing_requests() -> None:
    session, _transport = _session(
        {MCP_TOOLS_CALL_METHOD: [{"resultType": "input_required"}]}
    )

    with pytest.raises(MCPProtocolError) as excinfo:
        session.call_tool(remote_name="needs-input", arguments={})

    assert excinfo.value.reason_code == "mcp_input_required_payload_missing"


@pytest.mark.parametrize("result", [{}, {"resultType": ""}])
def test_modern_result_requires_result_type(result: dict[str, Any]) -> None:
    with pytest.raises(MCPModernFlowError) as excinfo:
        resolve_modern_result(
            method=MCP_TOOLS_LIST_METHOD,
            params={},
            result=result,
            request=lambda _method, _params: {},
            fulfill=lambda _method, _params: {},
            timeout_seconds=1.0,
        )

    assert excinfo.value.reason_code == "mcp_result_type_missing"


def test_modern_result_rejects_unknown_result_type() -> None:
    with pytest.raises(MCPModernFlowError) as excinfo:
        resolve_modern_result(
            method=MCP_TOOLS_LIST_METHOD,
            params={},
            result={"resultType": "future"},
            request=lambda _method, _params: {},
            fulfill=lambda _method, _params: {},
            timeout_seconds=1.0,
        )

    assert excinfo.value.reason_code == "mcp_result_type_unsupported"


def test_legacy_result_may_omit_result_type() -> None:
    session, _transport = _session({})
    result = {"tools": []}

    assert (
        session._resolve_response(
            method=MCP_TOOLS_LIST_METHOD,
            params={},
            result=result,
        )
        == result
    )
