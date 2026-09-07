"""MCP stdio client transport."""

import json
import os
from pathlib import Path
import selectors
import subprocess
import threading
import time
from typing import Any

from openminion.base.config.base import ConfigError
from openminion.base.config.env.subprocess import build_subprocess_env
from openminion.base.config.mcp import MCPServerConfig, resolve_mcp_server_env

from .auth import MCPTokenStore, read_token_ref
from .contracts import MCP_MODERN_PROTOCOL_VERSION
from .errors import MCPProtocolError, MCPServerUnavailableError, MCPTimeoutError
from .schemas import MCPHeaderBinding
from .transport_protocol import (
    build_server_request_response,
    dispatch_server_notification,
    extract_result_message,
    protocol_version_from_payload,
)


class StdioMCPTransport:
    """Minimal synchronous JSON-RPC-over-stdio transport for MCP."""

    def __init__(
        self,
        server: MCPServerConfig,
        *,
        token_store: MCPTokenStore | None = None,
    ) -> None:
        self._server = server
        self._token_store = token_store
        self._process: subprocess.Popen[bytes] | None = None
        self._selector = selectors.DefaultSelector()
        self._read_buffer = bytearray()
        self._stderr_buffer = bytearray()
        self._stderr_lock = threading.Lock()
        self._stderr_thread: threading.Thread | None = None
        self._stderr_stop = threading.Event()
        self._write_lock = threading.Lock()
        self._read_lock = threading.Lock()
        self._response_condition = threading.Condition()
        self._pending_responses: dict[Any, dict[str, Any]] = {}
        self._active_request_ids: set[Any] = set()
        self._next_request_id = 1

    @property
    def server_name(self) -> str:
        return self._server.name

    @property
    def authorization_identity(self) -> str:
        return ""

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        if self.is_running():
            return

        self._enforce_stdio_trust()
        env = self._build_stdio_env()
        cwd = self._resolve_stdio_cwd()
        process = subprocess.Popen(
            self._server.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            bufsize=0,
            close_fds=True,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.kill()
            process.wait(timeout=5)
            raise MCPServerUnavailableError(
                f"MCP server '{self.server_name}' did not expose stdio pipes.",
                reason_code="mcp_stdio_pipes_missing",
            )

        self._process = process
        self._read_buffer.clear()
        with self._stderr_lock:
            self._stderr_buffer.clear()
        self._stderr_stop.clear()
        self._selector.close()
        self._selector = selectors.DefaultSelector()
        self._selector.register(process.stdout, selectors.EVENT_READ)
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name=f"mcp-stderr-{self.server_name}",
            daemon=True,
        )
        self._stderr_thread.start()

    def _enforce_stdio_trust(self) -> None:
        sandbox = self._server.stdio_sandbox
        if sandbox.require_trust and not self._server.trusted:
            raise MCPServerUnavailableError(
                f"MCP stdio server '{self.server_name}' requires explicit trust.",
                reason_code="mcp_stdio_untrusted",
                details={"mcp_server": self.server_name},
            )

    def _resolve_stdio_cwd(self) -> str | None:
        cwd = self._server.cwd or None
        if not cwd:
            return None
        cwd = str(Path(cwd).expanduser().resolve(strict=False))
        allowlist = [
            Path(item).expanduser().resolve(strict=False)
            for item in self._server.stdio_sandbox.cwd_allowlist
        ]
        if not allowlist:
            return cwd
        resolved = Path(cwd)
        if any(resolved == item or item in resolved.parents for item in allowlist):
            return cwd
        raise MCPServerUnavailableError(
            f"MCP stdio server '{self.server_name}' cwd is outside the allowlist.",
            reason_code="mcp_stdio_cwd_denied",
            details={"mcp_server": self.server_name, "cwd": cwd},
        )

    def _build_stdio_env(self) -> dict[str, str]:
        allowlist = set(self._server.stdio_sandbox.env_allowlist)
        inherit_allowlist = set(self._server.stdio_sandbox.inherit_env_allowlist)
        env: dict[str, str] = build_subprocess_env(inherit_parent=inherit_allowlist)
        try:
            configured_env = resolve_mcp_server_env(
                self._server,
                secret_resolver=self._read_required_secret,
            )
        except ConfigError as exc:
            raise MCPServerUnavailableError(
                f"MCP stdio server '{self.server_name}' has invalid env config: {exc}",
                reason_code="mcp_stdio_env_denied",
                details={"mcp_server": self.server_name},
            ) from exc
        if allowlist:
            configured_env = {
                key: value for key, value in configured_env.items() if key in allowlist
            }
        env.update(configured_env)
        return env

    def _read_required_secret(self, ref: str) -> str:
        value = read_token_ref(self._token_store, ref)
        if value:
            return value
        raise MCPServerUnavailableError(
            f"MCP stdio server '{self.server_name}' secret reference {ref!r} is unavailable.",
            reason_code="mcp_stdio_secret_missing",
            details={"mcp_server": self.server_name, "secret_ref": ref},
        )

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": str(method or "").strip(),
        }
        if params is not None:
            payload["params"] = params
        with self._write_lock:
            self._write_message(payload)

    def set_tool_header_bindings(
        self, tool_name: str, bindings: tuple[MCPHeaderBinding, ...]
    ) -> None:
        del tool_name, bindings

    def cancel_request(self, request_id: int) -> None:
        del request_id

    def request(
        self,
        *,
        method: str,
        params: dict[str, Any] | None = None,
        timeout_seconds: float,
        server_request_handler: Any | None = None,
    ) -> dict[str, Any]:
        with self._write_lock:
            self.start()
            request_id = self._next_request_id
            self._next_request_id += 1
            with self._response_condition:
                self._active_request_ids.add(request_id)
            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": str(method or "").strip(),
            }
            if params is not None:
                payload["params"] = params
            try:
                self._write_message(payload)
            except MCPServerUnavailableError:
                with self._response_condition:
                    self._active_request_ids.discard(request_id)
                raise
        modern = protocol_version_from_payload(payload) == MCP_MODERN_PROTOCOL_VERSION
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        try:
            while True:
                message = self._take_pending_response(request_id)
                if message is None:
                    with self._read_lock:
                        message = self._take_pending_response(request_id)
                        if message is None:
                            message = self._read_message(deadline=deadline)
                if "method" in message:
                    self._handle_server_message(
                        message=message,
                        server_request_handler=server_request_handler,
                        modern=modern,
                    )
                    continue
                if message.get("id") != request_id:
                    self._park_response(message, deadline=deadline)
                    continue
                return extract_result_message(message=message, method=method)
        finally:
            with self._response_condition:
                self._active_request_ids.discard(request_id)
                self._pending_responses.pop(request_id, None)
                self._response_condition.notify_all()

    def _take_pending_response(self, request_id: Any) -> dict[str, Any] | None:
        with self._response_condition:
            message = self._pending_responses.pop(request_id, None)
            if message is not None:
                self._response_condition.notify_all()
            return message

    def _park_response(self, message: dict[str, Any], *, deadline: float) -> None:
        response_id = message.get("id")
        with self._response_condition:
            if response_id not in self._active_request_ids:
                return
            self._pending_responses[response_id] = message
            self._response_condition.notify_all()
            while response_id in self._pending_responses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                self._response_condition.wait(timeout=remaining)

    def _handle_server_message(
        self,
        *,
        message: dict[str, Any],
        server_request_handler: Any | None,
        modern: bool,
    ) -> None:
        method = str(message.get("method", "") or "").strip()
        if not method:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' sent a method message without a method.",
                reason_code="mcp_missing_method",
            )
        params = message.get("params", {})
        if not isinstance(params, dict):
            params = {}
        request_id = message.get("id")
        if request_id is None:
            dispatch_server_notification(
                handler=server_request_handler,
                method=method,
                params=dict(params),
            )
            return
        if modern:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' sent a server request on a modern stdio stream.",
                reason_code="mcp_modern_stdio_server_request",
            )
        response_payload = build_server_request_response(
            handler=server_request_handler,
            method=method,
            params=dict(params),
            request_id=request_id,
        )
        with self._write_lock:
            self._write_message(response_payload)

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        finally:
            self._selector.close()
            self._stderr_stop.set()
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=1)
            self._stderr_thread = None
            self._process = None
            self._read_buffer.clear()
            with self._response_condition:
                self._pending_responses.clear()
                self._active_request_ids.clear()
                self._response_condition.notify_all()

    def _write_message(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPServerUnavailableError(
                f"MCP server '{self.server_name}' is not running.",
                reason_code="mcp_server_unavailable",
                details=self._error_details(),
            )
        if process.poll() is not None:
            raise MCPServerUnavailableError(
                f"MCP server '{self.server_name}' is not available.",
                reason_code="mcp_server_unavailable",
                details=self._error_details(),
            )
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
        try:
            process.stdin.write(body + b"\n")
            process.stdin.flush()
        except BrokenPipeError as exc:
            raise MCPServerUnavailableError(
                f"MCP server '{self.server_name}' closed its stdin pipe.",
                reason_code="mcp_server_unavailable",
                details=self._error_details(),
            ) from exc

    def _read_message(self, *, deadline: float) -> dict[str, Any]:
        while not self._read_buffer:
            self._fill_read_buffer(deadline=deadline)
        if self._looks_like_lsp_message():
            return self._read_lsp_message(deadline=deadline)
        return self._read_ndjson_message(deadline=deadline)

    def _looks_like_lsp_message(self) -> bool:
        prefix = bytes(self._read_buffer[: min(len(self._read_buffer), 32)])
        return prefix.startswith(b"Content-Length")

    def _read_ndjson_message(self, *, deadline: float) -> dict[str, Any]:
        line = self._read_line(deadline=deadline)
        payload = line.strip()
        if not payload:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' returned an empty NDJSON message.",
                reason_code="mcp_ndjson_empty_message",
            )
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' returned invalid NDJSON.",
                reason_code="mcp_ndjson_invalid_json",
            ) from exc
        if not isinstance(decoded, dict):
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' returned a non-object message.",
                reason_code="mcp_non_object_message",
            )
        return decoded

    def _read_lsp_message(self, *, deadline: float) -> dict[str, Any]:
        headers: dict[str, str] = {}
        while True:
            line = self._read_line(deadline=deadline)
            stripped = line.strip()
            if not stripped:
                break
            if b":" not in line:
                raise MCPProtocolError(
                    f"MCP server '{self.server_name}' sent a malformed header line.",
                    reason_code="mcp_lsp_malformed_header",
                )
            raw_key, raw_value = line.split(b":", 1)
            headers[raw_key.decode("ascii", errors="ignore").strip().lower()] = (
                raw_value.decode("utf-8", errors="replace").strip()
            )

        content_length_raw = headers.get("content-length", "").strip()
        if not content_length_raw:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' omitted Content-Length.",
                reason_code="mcp_lsp_missing_content_length",
            )
        try:
            content_length = int(content_length_raw)
        except ValueError as exc:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' returned invalid Content-Length.",
                reason_code="mcp_lsp_invalid_content_length",
            ) from exc
        payload = self._read_exact(content_length, deadline=deadline)
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' returned invalid JSON.",
                reason_code="mcp_lsp_invalid_json",
            ) from exc
        if not isinstance(decoded, dict):
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' returned a non-object message.",
                reason_code="mcp_non_object_message",
            )
        return decoded

    def _read_line(self, *, deadline: float) -> bytes:
        while True:
            newline_index = self._read_buffer.find(b"\n")
            if newline_index >= 0:
                line = bytes(self._read_buffer[: newline_index + 1])
                del self._read_buffer[: newline_index + 1]
                return line
            self._fill_read_buffer(deadline=deadline)

    def _read_exact(self, length: int, *, deadline: float) -> bytes:
        while len(self._read_buffer) < length:
            self._fill_read_buffer(deadline=deadline)
        payload = bytes(self._read_buffer[:length])
        del self._read_buffer[:length]
        return payload

    def _fill_read_buffer(self, *, deadline: float) -> None:
        process = self._process
        if process is None or process.stdout is None:
            raise MCPServerUnavailableError(
                f"MCP server '{self.server_name}' is not running.",
                reason_code="mcp_server_unavailable",
                details=self._error_details(),
            )
        if process.poll() is not None:
            raise MCPServerUnavailableError(
                f"MCP server '{self.server_name}' is not available.",
                reason_code="mcp_server_unavailable",
                details=self._error_details(),
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MCPTimeoutError(
                f"MCP server '{self.server_name}' did not reply before timeout.",
                reason_code="mcp_timeout",
                details=self._error_details(),
            )

        events = self._selector.select(timeout=remaining)
        if not events:
            raise MCPTimeoutError(
                f"MCP server '{self.server_name}' did not reply before timeout.",
                reason_code="mcp_timeout",
                details=self._error_details(),
            )

        try:
            chunk = os.read(process.stdout.fileno(), 65536)
        except OSError as exc:
            raise MCPServerUnavailableError(
                f"MCP server '{self.server_name}' stdout is unavailable.",
                reason_code="mcp_server_unavailable",
                details=self._error_details(),
            ) from exc
        if not chunk:
            raise MCPServerUnavailableError(
                f"MCP server '{self.server_name}' closed its stdout pipe.",
                reason_code="mcp_server_unavailable",
                details=self._error_details(),
            )
        self._read_buffer.extend(chunk)

    def stderr_tail(self, *, limit: int = 4096) -> str:
        with self._stderr_lock:
            if not self._stderr_buffer:
                return ""
            payload = bytes(self._stderr_buffer[-max(1, int(limit)) :])
        return payload.decode("utf-8", errors="replace").strip()

    def _error_details(self) -> dict[str, Any]:
        tail = self.stderr_tail()
        return {"mcp_stderr_tail": tail} if tail else {}

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        buffer_limit = max(1024, int(self._server.stderr_buffer_bytes))
        while not self._stderr_stop.is_set():
            try:
                chunk = os.read(process.stderr.fileno(), 4096)
            except OSError:
                return
            if not chunk:
                return
            with self._stderr_lock:
                self._stderr_buffer.extend(chunk)
                if len(self._stderr_buffer) > buffer_limit:
                    del self._stderr_buffer[: len(self._stderr_buffer) - buffer_limit]
