from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.tools.mcp.interfaces import MCPClientCapabilityState
from openminion.tools.mcp.manager import MCPFleetManager
from openminion.tools.mcp.schemas import MCPSamplingResult


class _SamplingHandler:
    def sample(self, *, server_name: str, request) -> MCPSamplingResult:
        assert server_name == "fixture"
        assert request.max_tokens == 32
        return MCPSamplingResult(
            role="assistant",
            content={"type": "text", "text": "sampled from callback"},
            model="fixture-http-sampler",
            stop_reason="endTurn",
        )


class _CallbackHTTPHandler(BaseHTTPRequestHandler):
    server_version = "MCPHTTPCallbackFixture/1.0"

    def do_POST(self) -> None:  # noqa: N802
        owner = self.server  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        owner.requests.append(payload)
        method = str(payload.get("method", "") or "").strip()

        if payload.get("id") == "sampling-1" and "result" in payload and not method:
            owner.callback_payloads.append(payload)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {"ack": True},
                    }
                ).encode("utf-8")
            )
            return

        if method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return

        if method == "initialize":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {"tools": {}},
                            "serverInfo": {
                                "name": "http-callback-fixture",
                                "version": "1.0.0",
                            },
                        },
                    }
                ).encode("utf-8")
            )
            return

        if method == "tools/list":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {
                            "tools": [
                                {
                                    "name": "http-sampling-tool",
                                    "description": "Trigger nested sampling callback.",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {},
                                        "additionalProperties": False,
                                    },
                                }
                            ]
                        },
                    }
                ).encode("utf-8")
            )
            return

        if method == "prompts/list":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {"prompts": []},
                    }
                ).encode("utf-8")
            )
            return

        if method == "resources/list":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {"resources": []},
                    }
                ).encode("utf-8")
            )
            return

        if method == "tools/call":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            nested = {
                "jsonrpc": "2.0",
                "id": "sampling-1",
                "method": "sampling/createMessage",
                "params": {
                    "messages": [
                        {
                            "role": "user",
                            "content": {
                                "type": "text",
                                "text": "Say hello from HTTP callback.",
                            },
                        }
                    ],
                    "maxTokens": 32,
                },
            }
            terminal = {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "result": {
                    "content": [{"type": "text", "text": "http callback complete"}],
                    "structuredContent": {"callback": True},
                    "isError": False,
                },
            }
            for message in (nested, terminal):
                self.wfile.write(b"event: message\n")
                self.wfile.write(
                    f"data: {json.dumps(message, separators=(',', ':'))}\n\n".encode(
                        "utf-8"
                    )
                )
            self.wfile.write(b"event: end\n\n")
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return None


@contextmanager
def _callback_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CallbackHTTPHandler)
    server.requests = []  # type: ignore[attr-defined]
    server.callback_payloads = []  # type: ignore[attr-defined]
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


def test_http_sse_server_callback_round_trip_supports_sampling() -> None:
    with _callback_server() as server:
        manager = MCPFleetManager(
            servers=_runtime_config(
                f"http://127.0.0.1:{server.server_port}/mcp"
            ).mcp_servers,
            client_capability_state=MCPClientCapabilityState(
                sampling_handler=_SamplingHandler(),
            ),
        )
        try:
            discovered = manager.discover_tools()
            assert [tool.remote_name for tool in discovered] == ["http-sampling-tool"]

            result = manager.call_tool(
                server_name="fixture",
                remote_name="http-sampling-tool",
                arguments={},
            )
            assert result["ok"] is True
            assert result["content"] == "http callback complete"

            assert len(server.callback_payloads) == 1
            callback = server.callback_payloads[0]
            assert callback["id"] == "sampling-1"
            assert callback["result"]["model"] == "fixture-http-sampler"
            assert callback["result"]["content"]["text"] == "sampled from callback"
        finally:
            manager.close()
