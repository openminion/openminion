from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.tools.mcp.manager import MCPFleetManager, MCPProtocolError


class _ProtocolVersionHandler(BaseHTTPRequestHandler):
    server_version = "MCPProtocolVersionFixture/1.0"

    def do_POST(self) -> None:  # noqa: N802
        owner = self.server  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        method = str(payload.get("method", "") or "").strip()
        owner.requests.append(
            {
                "method": method,
                "headers": dict(self.headers.items()),
                "payload": payload,
            }
        )

        if method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return

        response = _response_for(payload, protocol_version=owner.protocol_version)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return None


def _response_for(payload: dict[str, Any], *, protocol_version: str) -> dict[str, Any]:
    method = str(payload.get("method", "") or "").strip()
    request_id = payload.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "version-fixture", "version": "1.0.0"},
            },
        }
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "echo-text",
                        "description": "Echo tool.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    }
                ]
            },
        }
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"prompts": []}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": []}}
    if method == "tools/call":
        params = dict(payload.get("params", {}) or {})
        arguments = dict(params.get("arguments", {}) or {})
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {"type": "text", "text": f"echo: {arguments.get('text', '')}"}
                ],
                "structuredContent": arguments,
                "isError": False,
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": method},
    }


@contextmanager
def _protocol_server(protocol_version: str):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProtocolVersionHandler)
    server.protocol_version = protocol_version  # type: ignore[attr-defined]
    server.requests = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _runtime_config(url: str) -> RuntimeConfig:
    return RuntimeConfig(
        mcp_servers=[
            MCPServerConfig(
                name="Fixture",
                transport="streamable_http",
                url=url,
                request_timeout_seconds=5.0,
                startup_timeout_seconds=5.0,
            )
        ]
    )


def test_negotiated_protocol_version_is_reused_in_followup_http_headers() -> None:
    with _protocol_server("2025-04-01") as server:
        manager = MCPFleetManager.from_runtime_config(
            _runtime_config(f"http://127.0.0.1:{server.server_port}/mcp")
        )
        try:
            discovered = manager.discover_tools()
            assert discovered[0].remote_name == "echo-text"

            manager.call_tool(
                server_name="fixture",
                remote_name="echo-text",
                arguments={"text": "hello"},
            )
            tools_list_request = next(
                item for item in server.requests if item["method"] == "tools/list"
            )
            call_request = next(
                item for item in server.requests if item["method"] == "tools/call"
            )
            tools_headers = {
                key.lower(): value
                for key, value in tools_list_request["headers"].items()
            }
            call_headers = {
                key.lower(): value for key, value in call_request["headers"].items()
            }
            assert tools_headers["mcp-protocol-version"] == "2025-04-01"
            assert call_headers["mcp-protocol-version"] == "2025-04-01"
        finally:
            manager.close()


def test_supported_older_protocol_version_succeeds() -> None:
    with _protocol_server("2025-03-26") as server:
        manager = MCPFleetManager.from_runtime_config(
            _runtime_config(f"http://127.0.0.1:{server.server_port}/mcp")
        )
        try:
            discovered = manager.discover_tools()
            assert {tool.remote_name for tool in discovered} == {"echo-text"}
        finally:
            manager.close()


def test_below_floor_protocol_version_raises_typed_error() -> None:
    with _protocol_server("2025-01-01") as server:
        manager = MCPFleetManager.from_runtime_config(
            _runtime_config(f"http://127.0.0.1:{server.server_port}/mcp")
        )
        try:
            with pytest.raises(MCPProtocolError) as excinfo:
                manager.discover_tools()
            assert excinfo.value.reason_code == "mcp_protocol_version_too_old"
        finally:
            manager.close()
