from __future__ import annotations

import json
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.tools.mcp.interfaces import MCPProgressListener
from openminion.tools.mcp.manager import MCPCallError, MCPFleetManager, MCPServerSession


FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
)


class _ProgressListener(MCPProgressListener):
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def progress_updated(
        self,
        *,
        server_name: str,
        progress_token: str,
        progress: float | None,
        message: str = "",
    ) -> None:
        with self._lock:
            self.events.append(
                {
                    "server_name": server_name,
                    "progress_token": progress_token,
                    "progress": progress,
                    "message": message,
                }
            )


class _CancelHTTPHandler(BaseHTTPRequestHandler):
    server_version = "MCPCancelFixture/1.0"

    def do_POST(self) -> None:  # noqa: N802
        owner = self.server  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        owner.requests.append(payload)
        method = str(payload.get("method", "") or "").strip()

        if method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return

        if method == "notifications/cancelled":
            request_id = str(
                (payload.get("params", {}) or {}).get("requestId", "") or ""
            )
            if request_id:
                owner.cancelled.add(request_id)
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
                                "name": "cancel-fixture",
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
                                    "name": "sleep-tool",
                                    "description": "Long-running tool over SSE.",
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
            request_id = str(payload.get("id", "") or "")
            progress_token = (
                str(
                    (
                        ((payload.get("params", {}) or {}).get("_meta", {}) or {}).get(
                            "progressToken", ""
                        )
                        or ""
                    )
                ).strip()
                or request_id
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            progress_message = {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {
                    "progressToken": progress_token,
                    "progress": 0.25,
                    "message": "working",
                },
            }
            self.wfile.write(b"event: message\n")
            self.wfile.write(
                f"data: {json.dumps(progress_message, separators=(',', ':'))}\n\n".encode(
                    "utf-8"
                )
            )
            self.wfile.flush()
            time.sleep(0.3)
            cancelled = request_id in owner.cancelled
            terminal = {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "cancelled" if cancelled else "finished",
                        }
                    ],
                    "structuredContent": {"cancelled": cancelled},
                    "isError": cancelled,
                },
            }
            self.wfile.write(b"event: message\n")
            self.wfile.write(
                f"data: {json.dumps(terminal, separators=(',', ':'))}\n\n".encode(
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
def _cancel_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CancelHTTPHandler)
    server.requests = []  # type: ignore[attr-defined]
    server.cancelled = set()  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        mcp_servers=[
            MCPServerConfig(
                name="Fixture",
                transport="stdio",
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                request_timeout_seconds=5.0,
                startup_timeout_seconds=5.0,
                env={"MOCK_MCP_LONG_TOOL_SECONDS": "0.6"},
            )
        ]
    )


def test_cancel_emits_notifications_cancelled_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MCPServerSession(_runtime_config().mcp_servers[0])
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def _capture(method: str, params: dict[str, Any] | None = None) -> None:
        calls.append((method, params))

    monkeypatch.setattr(session._transport, "notify", _capture)
    session.cancel(7)

    assert calls == [("notifications/cancelled", {"requestId": 7})]


def test_progress_notifications_dispatch_to_listener() -> None:
    listener = _ProgressListener()
    session = MCPServerSession(
        _runtime_config().mcp_servers[0],
        progress_listener=listener,
    )

    session._handle_server_notification(
        method="notifications/progress",
        params={
            "progressToken": "req-1",
            "progress": 0.5,
            "message": "halfway",
        },
    )

    assert listener.events == [
        {
            "server_name": "fixture",
            "progress_token": "req-1",
            "progress": 0.5,
            "message": "halfway",
        }
    ]


def test_long_running_http_tool_can_be_cancelled_and_reports_progress() -> None:
    listener = _ProgressListener()
    with _cancel_server() as server:
        manager = MCPFleetManager(
            servers=[
                MCPServerConfig(
                    name="Fixture",
                    transport="streamable_http",
                    url=f"http://127.0.0.1:{server.server_port}/mcp",
                    request_timeout_seconds=5.0,
                    startup_timeout_seconds=5.0,
                )
            ],
            progress_listener=listener,
        )
        try:
            manager.discover_tools()
            errors: list[BaseException] = []

            def _invoke() -> None:
                try:
                    manager.call_tool(
                        server_name="fixture",
                        remote_name="sleep-tool",
                        arguments={},
                        progress_token="req-1",
                    )
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            worker = threading.Thread(target=_invoke, daemon=True)
            worker.start()
            time.sleep(0.1)
            manager._sessions["fixture"].cancel(3)
            worker.join(timeout=5)

            assert worker.is_alive() is False
            assert len(errors) == 1
            exc = errors[0]
            assert isinstance(exc, MCPCallError)
            assert exc.reason_code == "mcp_client_cancelled"
            assert any(event["progress_token"] == "req-1" for event in listener.events)
            assert any((event["progress"] or 0.0) > 0 for event in listener.events)
        finally:
            manager.close()
