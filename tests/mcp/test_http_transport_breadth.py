from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import parse as urllib_parse

import pytest

from openminion.base.config.mcp import MCPAuthorizationConfig, MCPServerConfig
from openminion.base.config import OpenMinionConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.tools.mcp.manager import (
    MCPAuthorizationError,
    MCPFleetManager,
)
from openminion.tools.mcp.auth import (
    InMemoryMCPTokenStore,
    build_authorization_url,
    build_pkce_challenge,
    discover_oauth_metadata,
    exchange_authorization_code,
    register_oauth_client,
    revoke_oauth_token,
)
from openminion.tools.mcp.transport import (
    MCPRemoteTransportError,
    StreamableHTTPMCPTransport,
    parse_www_authenticate,
)


class _RemoteMCPHandler(BaseHTTPRequestHandler):
    server_version = "RemoteMCPFixture/1.0"

    def do_POST(self) -> None:  # noqa: N802
        owner = self.server  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        if self.path == "/token":
            fields = {
                key: values[0] if values else ""
                for key, values in urllib_parse.parse_qs(raw.decode("utf-8")).items()
            }
            owner.token_requests.append(fields)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "access_token": "fresh-token",
                        "refresh_token": "rotated-refresh-token",
                        "expires_in": 3600,
                        "scope": "mcp.read",
                    }
                ).encode("utf-8")
            )
            return
        if self.path == "/register":
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            owner.registration_requests.append(payload)
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"client_id": "registered-client"}).encode("utf-8")
            )
            return
        if self.path == "/revoke":
            owner.revocation_requests.append(raw.decode("utf-8"))
            self.send_response(200)
            self.end_headers()
            return
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        method = str(payload.get("method", "") or "").strip()
        owner.last_requests.append(
            {
                "method": method,
                "headers": dict(self.headers.items()),
                "payload": payload,
            }
        )

        if (
            getattr(owner, "reject_session", False)
            and str(self.headers.get("Mcp-Session-Id", "") or "").strip()
        ):
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32003, "message": "invalid session"},
                    }
                ).encode("utf-8")
            )
            return

        expected_token = getattr(owner, "expected_bearer_token", "")
        auth_header = str(self.headers.get("Authorization", "") or "").strip()
        if expected_token and auth_header != f"Bearer {expected_token}":
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="mcp-fixture"')
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32001, "message": "unauthorized"},
                    }
                ).encode("utf-8")
            )
            return

        if method == "notifications/initialized":
            self.send_response(202)
            self._send_session_header()
            self.end_headers()
            return

        if method in getattr(owner, "sse_methods", set()):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self._send_session_header()
            self.end_headers()
            self.wfile.write(b"event: message\n")
            self.wfile.write(b"data: ")
            self.wfile.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": _response_for_request(payload).get("result", {}),
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            self.wfile.write(b"\n\n")
            return

        response = _response_for_request(payload)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._send_session_header()
        self.end_headers()
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        owner = self.server  # type: ignore[attr-defined]
        if self.path == "/.well-known/oauth-authorization-server":
            base_url = f"http://127.0.0.1:{owner.server_port}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "issuer": base_url,
                        "authorization_endpoint": f"{base_url}/authorize",
                        "token_endpoint": f"{base_url}/token",
                        "registration_endpoint": f"{base_url}/register",
                        "revocation_endpoint": f"{base_url}/revoke",
                    }
                ).encode("utf-8")
            )
            return
        owner.last_requests.append(
            {
                "method": "GET",
                "headers": dict(self.headers.items()),
                "payload": {},
            }
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self._send_session_header()
        self.end_headers()
        self.wfile.write(b"event: message\n")
        self.wfile.write(
            b'data: {"jsonrpc":"2.0","method":"notifications/tools/list_changed","params":{"reason":"fixture"}}\n\n'
        )
        self.wfile.write(b"event: end\n\n")

    def do_DELETE(self) -> None:  # noqa: N802
        owner = self.server  # type: ignore[attr-defined]
        owner.last_requests.append(
            {
                "method": "DELETE",
                "headers": dict(self.headers.items()),
                "payload": {},
            }
        )
        self.send_response(202)
        self.end_headers()

    def _send_session_header(self) -> None:
        owner = self.server  # type: ignore[attr-defined]
        session_id = str(getattr(owner, "session_id", "") or "").strip()
        if session_id:
            self.send_header("Mcp-Session-Id", session_id)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return None


def _response_for_request(payload: dict[str, Any]) -> dict[str, Any]:
    method = str(payload.get("method", "") or "").strip()
    request_id = payload.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "remote-mcp-fixture", "version": "1.0.0"},
            },
        }
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "remote-echo",
                        "description": "Echo over remote MCP transport.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                            },
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    }
                ]
            },
        }
    if method == "prompts/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"prompts": []},
        }
    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"resources": []},
        }
    if method == "tools/call":
        params = dict(payload.get("params", {}) or {})
        arguments = dict(params.get("arguments", {}) or {})
        text = str(arguments.get("text", "") or "")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": f"remote: {text}"}],
                "structuredContent": {"echo": text},
                "isError": False,
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


@contextmanager
def _remote_mcp_server(
    *,
    expected_bearer_token: str = "",
    sse_methods: set[str] | None = None,
    session_id: str = "",
    reject_session: bool = False,
):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RemoteMCPHandler)
    server.expected_bearer_token = expected_bearer_token
    server.sse_methods = set(sse_methods or ())
    server.session_id = session_id
    server.reject_session = reject_session
    server.last_requests = []
    server.token_requests = []
    server.registration_requests = []
    server.revocation_requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _http_runtime_config(
    *,
    url: str,
    authorization: MCPAuthorizationConfig | None = None,
) -> RuntimeConfig:
    return RuntimeConfig(
        mcp_servers=[
            MCPServerConfig(
                name="Fixture",
                transport="streamable_http",
                url=url,
                authorization=authorization or MCPAuthorizationConfig(),
                request_timeout_seconds=5.0,
                startup_timeout_seconds=5.0,
            )
        ]
    )


def _close_bootstrap(bootstrap) -> None:
    manager = getattr(bootstrap, "mcp_manager", None)
    if manager is not None:
        manager.close()


def test_streamable_http_transport_registers_and_calls_remote_tool() -> None:
    with _remote_mcp_server(expected_bearer_token="secret-token") as server:
        config = _http_runtime_config(
            url=f"http://127.0.0.1:{server.server_port}/mcp",
            authorization=MCPAuthorizationConfig(
                mode="bearer",
                bearer_token="secret-token",
            ),
        )
        bootstrap = build_runtime_bootstrap(config=config, strict=True)
        try:
            tool_names = set(bootstrap.registry.list().keys())
            assert "mcp.fixture.remote_echo" in tool_names

            batch = bootstrap.registry.execute_calls(
                [
                    ProviderToolCall(
                        name="mcp.fixture.remote_echo",
                        arguments={"text": "hello remote"},
                        source="native",
                    )
                ],
                context=ToolExecutionContext(
                    channel="console",
                    target="unit-test",
                    session_id="session-remote-mcp",
                    metadata={"tool_call_origin": "model"},
                ),
            )
            assert len(batch.results) == 1
            result = batch.results[0]
            assert result.ok is True
            assert result.content.startswith("remote: hello remote")

            methods = [item["method"] for item in server.last_requests]
            assert methods == [
                "initialize",
                "notifications/initialized",
                "tools/list",
                "prompts/list",
                "resources/list",
                "resources/templates/list",
                "tools/call",
            ]
            call_request = server.last_requests[-1]
            assert call_request["headers"]["Authorization"] == "Bearer secret-token"
            assert call_request["headers"]["Mcp-Method"] == "tools/call"
            assert call_request["headers"]["Mcp-Name"] == "remote-echo"
        finally:
            _close_bootstrap(bootstrap)


def test_streamable_http_transport_reuses_session_id_and_closes() -> None:
    with _remote_mcp_server(session_id="session-123") as server:
        transport = StreamableHTTPMCPTransport(
            _http_runtime_config(
                url=f"http://127.0.0.1:{server.server_port}/mcp"
            ).mcp_servers[0]
        )
        try:
            transport.request(method="initialize", params={}, timeout_seconds=5.0)
            assert transport.session_state.session_id == "session-123"
            transport.notify("notifications/initialized", {})
            transport.request(method="tools/list", params={}, timeout_seconds=5.0)
            transport.close()

            assert server.last_requests[1]["headers"]["Mcp-Session-Id"] == (
                "session-123"
            )
            assert server.last_requests[2]["headers"]["Mcp-Session-Id"] == (
                "session-123"
            )
            assert server.last_requests[-1]["method"] == "DELETE"
            assert server.last_requests[-1]["headers"]["Mcp-Session-Id"] == (
                "session-123"
            )
            assert transport.session_state.session_id == ""
        finally:
            transport.close()


def test_streamable_http_transport_invalid_session_fails_deterministically() -> None:
    with _remote_mcp_server(session_id="expired", reject_session=True) as server:
        transport = StreamableHTTPMCPTransport(
            _http_runtime_config(
                url=f"http://127.0.0.1:{server.server_port}/mcp"
            ).mcp_servers[0]
        )
        transport.request(method="initialize", params={}, timeout_seconds=5.0)

        with pytest.raises(MCPRemoteTransportError) as excinfo:
            transport.request(method="tools/list", params={}, timeout_seconds=5.0)

        assert excinfo.value.reason_code == "mcp_http_session_invalid"
        assert transport.session_state.session_id == ""


def test_streamable_http_transport_resume_event_stream_dispatches_notifications() -> (
    None
):
    class _Listener:
        def __init__(self) -> None:
            self.notifications: list[tuple[str, dict[str, Any]]] = []

        def handle_notification(self, *, method: str, params: dict[str, Any]) -> None:
            self.notifications.append((method, params))

    with _remote_mcp_server(session_id="resume-123") as server:
        transport = StreamableHTTPMCPTransport(
            _http_runtime_config(
                url=f"http://127.0.0.1:{server.server_port}/mcp"
            ).mcp_servers[0]
        )
        listener = _Listener()
        try:
            transport.request(method="initialize", params={}, timeout_seconds=5.0)
            messages = transport.resume_event_stream(
                timeout_seconds=5.0,
                last_event_id="event-7",
                server_request_handler=listener,
            )

            assert messages[0]["method"] == "notifications/tools/list_changed"
            assert listener.notifications == [
                ("notifications/tools/list_changed", {"reason": "fixture"})
            ]
            get_request = server.last_requests[-1]
            assert get_request["method"] == "GET"
            assert get_request["headers"]["Mcp-Session-Id"] == "resume-123"
            assert get_request["headers"]["Last-Event-Id"] == "event-7"
        finally:
            transport.close()


def test_streamable_http_transport_raises_typed_authorization_error() -> None:
    with _remote_mcp_server(expected_bearer_token="secret-token") as server:
        manager = MCPFleetManager.from_runtime_config(
            _http_runtime_config(
                url=f"http://127.0.0.1:{server.server_port}/mcp",
                authorization=MCPAuthorizationConfig(
                    mode="bearer",
                    bearer_token="wrong-token",
                ),
            )
        )
        try:
            with pytest.raises(MCPAuthorizationError) as excinfo:
                manager.discover_tools()
            assert excinfo.value.status_code == 401
            assert 'Bearer realm="mcp-fixture"' == excinfo.value.www_authenticate
            assert excinfo.value.auth_challenge == {
                "scheme": "Bearer",
                "realm": "mcp-fixture",
            }
        finally:
            manager.close()


def test_streamable_http_transport_uses_oauth_pkce_access_token_header() -> None:
    with _remote_mcp_server(expected_bearer_token="oauth-access-token") as server:
        manager = MCPFleetManager.from_runtime_config(
            _http_runtime_config(
                url=f"http://127.0.0.1:{server.server_port}/mcp",
                authorization=MCPAuthorizationConfig(
                    mode="oauth_pkce",
                    client_id="openminion-test",
                    authorization_server_metadata_url=(
                        "https://auth.example/.well-known/oauth-authorization-server"
                    ),
                    access_token="oauth-access-token",
                    refresh_token_ref="secret://mcp/fixture/refresh",
                ),
            )
        )
        try:
            manager.discover_tools()
            initialize_request = server.last_requests[0]  # type: ignore[attr-defined]
            assert (
                initialize_request["headers"]["Authorization"]
                == "Bearer oauth-access-token"
            )
        finally:
            manager.close()


def test_oauth_pkce_metadata_dcr_callback_and_revocation_helpers() -> None:
    with _remote_mcp_server() as server:
        base_url = f"http://127.0.0.1:{server.server_port}"
        config = MCPAuthorizationConfig(
            mode="oauth_pkce",
            client_id="openminion-test",
            authorization_server_metadata_url=(
                f"{base_url}/.well-known/oauth-authorization-server"
            ),
            redirect_uri="http://127.0.0.1/callback",
            scope="mcp.read",
        )

        metadata = discover_oauth_metadata(config, timeout_seconds=5.0)
        assert metadata.token_endpoint == f"{base_url}/token"
        assert metadata.registration_endpoint == f"{base_url}/register"

        registration = register_oauth_client(
            metadata=metadata,
            client_name="OpenMinion Test",
            redirect_uris=[config.redirect_uri],
            timeout_seconds=5.0,
        )
        assert registration["client_id"] == "registered-client"
        assert server.registration_requests[0]["redirect_uris"] == [
            "http://127.0.0.1/callback"
        ]

        challenge = build_pkce_challenge()
        authorization_url = build_authorization_url(
            config=config,
            metadata=metadata,
            challenge=challenge,
            state="state-123",
        )
        assert "code_challenge=" in authorization_url
        assert "state=state-123" in authorization_url

        token_state = exchange_authorization_code(
            config=config,
            metadata=metadata,
            code="callback-code",
            challenge=challenge,
            timeout_seconds=5.0,
        )
        assert token_state.access_token == "fresh-token"
        assert server.token_requests[-1]["grant_type"] == "authorization_code"
        assert server.token_requests[-1]["code"] == "callback-code"

        assert revoke_oauth_token(
            metadata=metadata,
            token=token_state.access_token,
            timeout_seconds=5.0,
        )
        assert "token=fresh-token" in server.revocation_requests[-1]


def test_oauth_pkce_refreshes_revoked_access_token_via_token_store() -> None:
    with _remote_mcp_server(expected_bearer_token="fresh-token") as server:
        base_url = f"http://127.0.0.1:{server.server_port}"
        token_store = InMemoryMCPTokenStore(
            {
                "secret://mcp/fixture/access": "expired-token",
                "secret://mcp/fixture/refresh": "refresh-token",
            }
        )
        transport = StreamableHTTPMCPTransport(
            _http_runtime_config(
                url=f"{base_url}/mcp",
                authorization=MCPAuthorizationConfig(
                    mode="oauth_pkce",
                    client_id="openminion-test",
                    authorization_server_metadata_url=(
                        f"{base_url}/.well-known/oauth-authorization-server"
                    ),
                    access_token_ref="secret://mcp/fixture/access",
                    refresh_token_ref="secret://mcp/fixture/refresh",
                ),
            ).mcp_servers[0],
            token_store=token_store,
        )
        try:
            transport.request(method="initialize", params={}, timeout_seconds=5.0)
            assert token_store.get("secret://mcp/fixture/access") == "fresh-token"
            assert (
                token_store.get("secret://mcp/fixture/refresh")
                == "rotated-refresh-token"
            )
            assert server.token_requests[-1]["grant_type"] == "refresh_token"
            assert server.last_requests[-1]["headers"]["Authorization"] == (
                "Bearer fresh-token"
            )
        finally:
            transport.close()


def test_oauth_pkce_config_exports_without_token_secrets() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "runtime": {
                "mcp_servers": [
                    {
                        "name": "Fixture",
                        "transport": "streamable_http",
                        "url": "https://mcp.example/messages",
                        "authorization": {
                            "mode": "oauth_pkce",
                            "client_id": "client-1",
                            "authorization_server_metadata_url": (
                                "https://auth.example/.well-known/oauth-authorization-server"
                            ),
                            "access_token": "raw-access-token",
                            "refresh_token_ref": "secret://mcp/fixture/refresh",
                        },
                    }
                ]
            },
            "agents": {"default": {"provider": "echo"}},
            "default_agent": "default",
        }
    )

    exported = config.to_dict()
    auth = exported["runtime"]["mcp_servers"][0]["authorization"]
    assert auth["mode"] == "oauth_pkce"
    assert auth["access_token"] == "<redacted>"
    assert auth["refresh_token_ref"] == "secret://mcp/fixture/refresh"
    assert "raw-access-token" not in str(exported)


def test_www_authenticate_parser_extracts_oauth_metadata_fields() -> None:
    assert parse_www_authenticate(
        'Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource", error="invalid_token"'
    ) == {
        "scheme": "Bearer",
        "resource_metadata": "https://mcp.example/.well-known/oauth-protected-resource",
        "error": "invalid_token",
    }


def test_streamable_http_transport_accepts_sse_response() -> None:
    with _remote_mcp_server(sse_methods={"tools/list"}) as server:
        manager = MCPFleetManager.from_runtime_config(
            _http_runtime_config(url=f"http://127.0.0.1:{server.server_port}/mcp")
        )
        try:
            discovered = manager.discover_tools()
            assert len(discovered) == 1
            assert discovered[0].remote_name == "remote-echo"
        finally:
            manager.close()
