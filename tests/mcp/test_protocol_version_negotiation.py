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

        modern_mode = str(getattr(owner, "modern_mode", "") or "")
        if method == "server/discover":
            if modern_mode == "method_not_allowed":
                self.send_response(405)
                self.end_headers()
                return
            if modern_mode == "success":
                response = {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {
                        "resultType": "complete",
                        "supportedVersions": ["2026-07-28"],
                        "capabilities": {"tools": {}},
                    },
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode("utf-8"))
                return
            if modern_mode == "unsupported":
                response = {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "error": {
                        "code": -32022,
                        "message": "unsupported version",
                        "data": {
                            "requested": "2026-07-28",
                            "supported": ["2026-07-28"],
                        },
                    },
                }
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode("utf-8"))
                return
            if modern_mode == "header_error":
                response = {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "error": {"code": -32020, "message": "header mismatch"},
                }
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode("utf-8"))
                return
            if modern_mode == "unrecognized_error":
                response = {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "error": {"code": -32000, "message": "legacy gateway error"},
                }
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode("utf-8"))
                return
            self.send_response(404)
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
        result = {
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
        }
        params = payload.get("params", {})
        meta = params.get("_meta", {}) if isinstance(params, dict) else {}
        if isinstance(meta, dict) and meta.get(
            "io.modelcontextprotocol/protocolVersion"
        ) == "2026-07-28":
            result.update(
                {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
            )
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
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
def _protocol_server(protocol_version: str, *, modern_mode: str = ""):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProtocolVersionHandler)
    server.protocol_version = protocol_version  # type: ignore[attr-defined]
    server.modern_mode = modern_mode  # type: ignore[attr-defined]
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
    with _protocol_server("2025-06-18") as server:
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
            assert tools_headers["mcp-protocol-version"] == "2025-06-18"
            assert call_headers["mcp-protocol-version"] == "2025-06-18"
            assert [item["method"] for item in server.requests[:2]] == [
                "server/discover",
                "initialize",
            ]
        finally:
            manager.close()


@pytest.mark.parametrize("version", ["2025-11-25", "2025-03-26"])
def test_supported_older_protocol_version_succeeds(version: str) -> None:
    with _protocol_server(version) as server:
        manager = MCPFleetManager.from_runtime_config(
            _runtime_config(f"http://127.0.0.1:{server.server_port}/mcp")
        )
        try:
            discovered = manager.discover_tools()
            assert {tool.remote_name for tool in discovered} == {"echo-text"}
        finally:
            manager.close()


@pytest.mark.parametrize("version", ["2025-04-01", "2099-01-01"])
def test_unknown_protocol_version_raises_typed_error(version: str) -> None:
    with _protocol_server(version) as server:
        manager = MCPFleetManager.from_runtime_config(
            _runtime_config(f"http://127.0.0.1:{server.server_port}/mcp")
        )
        try:
            with pytest.raises(MCPProtocolError) as excinfo:
                manager.discover_tools()
            assert excinfo.value.reason_code == "mcp_protocol_version_unsupported"
        finally:
            manager.close()


@pytest.mark.parametrize("modern_mode", ["success", "unsupported"])
def test_modern_http_negotiation_never_initializes(modern_mode: str) -> None:
    with _protocol_server("2025-11-25", modern_mode=modern_mode) as server:
        manager = MCPFleetManager.from_runtime_config(
            _runtime_config(f"http://127.0.0.1:{server.server_port}/mcp")
        )
        try:
            assert manager.discover_tools()
            assert server.requests[0]["method"] == "server/discover"
            assert not any(item["method"] == "initialize" for item in server.requests)
        finally:
            manager.close()


def test_recognized_modern_http_error_does_not_downgrade() -> None:
    with _protocol_server("2025-11-25", modern_mode="header_error") as server:
        manager = MCPFleetManager.from_runtime_config(
            _runtime_config(f"http://127.0.0.1:{server.server_port}/mcp")
        )
        try:
            with pytest.raises(MCPProtocolError) as excinfo:
                manager.discover_tools()
            assert excinfo.value.details["code"] == -32020
            assert not any(item["method"] == "initialize" for item in server.requests)
        finally:
            manager.close()


def test_unrecognized_http_error_downgrades_to_legacy() -> None:
    with _protocol_server("2025-11-25", modern_mode="unrecognized_error") as server:
        manager = MCPFleetManager.from_runtime_config(
            _runtime_config(f"http://127.0.0.1:{server.server_port}/mcp")
        )
        try:
            assert manager.discover_tools()
            assert [item["method"] for item in server.requests[:2]] == [
                "server/discover",
                "initialize",
            ]
        finally:
            manager.close()


def test_method_not_allowed_http_error_downgrades_to_legacy() -> None:
    with _protocol_server("2025-11-25", modern_mode="method_not_allowed") as server:
        manager = MCPFleetManager.from_runtime_config(
            _runtime_config(f"http://127.0.0.1:{server.server_port}/mcp")
        )
        try:
            assert manager.discover_tools()
            assert [item["method"] for item in server.requests[:2]] == [
                "server/discover",
                "initialize",
            ]
        finally:
            manager.close()
