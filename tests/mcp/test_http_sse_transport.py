from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.tools.mcp.manager import MCPFleetManager
from openminion.tools.mcp.transport import MCPProtocolError


class _SSEHandler(BaseHTTPRequestHandler):
    server_version = "MCPSSEFixture/1.0"

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

        if method in owner.malformed_methods:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"event: message\n")
            self.wfile.write(b"data: {not-json}\n\n")
            return

        if method in owner.sse_methods:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            response = _response_for(payload)
            self.wfile.write(b"event: message\n")
            self.wfile.write(
                f"data: {json.dumps(response, separators=(',', ':'))}\n\n".encode(
                    "utf-8"
                )
            )
            self.wfile.write(b"event: end\n\n")
            return

        response = _response_for(payload)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return None


def _response_for(payload: dict[str, Any]) -> dict[str, Any]:
    method = str(payload.get("method", "") or "").strip()
    request_id = payload.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sse-fixture", "version": "1.0.0"},
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
                        "description": "Echo over SSE.",
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
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": method},
    }


@contextmanager
def _sse_server(
    *, sse_methods: set[str] | None = None, malformed_methods: set[str] | None = None
):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SSEHandler)
    server.sse_methods = set(sse_methods or ())  # type: ignore[attr-defined]
    server.malformed_methods = set(malformed_methods or ())  # type: ignore[attr-defined]
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


def test_streamable_http_transport_supports_sse_initialize_and_tools_list() -> None:
    with _sse_server(sse_methods={"initialize", "tools/list"}) as server:
        manager = MCPFleetManager.from_runtime_config(
            _runtime_config(f"http://127.0.0.1:{server.server_port}/mcp")
        )
        try:
            discovered = manager.discover_tools()
            assert len(discovered) == 1
            assert discovered[0].remote_name == "remote-echo"
            methods = [item["method"] for item in server.requests]
            assert methods[:3] == [
                "initialize",
                "notifications/initialized",
                "tools/list",
            ]
        finally:
            manager.close()


def test_streamable_http_transport_surfaces_malformed_sse_as_protocol_error() -> None:
    with _sse_server(malformed_methods={"tools/list"}) as server:
        manager = MCPFleetManager.from_runtime_config(
            _runtime_config(f"http://127.0.0.1:{server.server_port}/mcp")
        )
        try:
            with pytest.raises(MCPProtocolError) as excinfo:
                manager.discover_tools()
            assert excinfo.value.reason_code == "mcp_sse_parse_error"
        finally:
            manager.close()
