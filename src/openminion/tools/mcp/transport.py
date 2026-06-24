"""MCP client transport."""

import json
import os
import selectors
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from openminion.base.config.base import ConfigError
from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.mcp import resolve_mcp_server_env

from .auth import MCPTokenStore, discover_oauth_metadata, refresh_oauth_access_token
from .contracts import MCP_PROTOCOL_VERSION
from .transport_protocol import build_server_request_response
from .transport_protocol import dispatch_server_notification
from .transport_protocol import extract_result_message
from .transport_protocol import mcp_name_header
from .transport_protocol import parse_sse_messages
from .transport_protocol import parse_www_authenticate
from .transport_protocol import protocol_version_from_payload


class MCPTransportError(RuntimeError):
    """Base transport error for MCP lifecycle failures."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "").strip()
        self.details = dict(details or {})


class MCPServerUnavailableError(MCPTransportError):
    """Raised when the MCP server process or endpoint is unavailable."""


class MCPTimeoutError(MCPTransportError):
    """Raised when the MCP server does not reply before the deadline."""


class MCPProtocolError(MCPTransportError):
    """Raised when the MCP server returns malformed protocol data."""


class MCPAuthorizationError(MCPProtocolError):
    """Raised when remote MCP authorization fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 401,
        www_authenticate: str = "",
        reason_code: str = "mcp_authorization_error",
    ) -> None:
        super().__init__(message, reason_code=reason_code)
        self.status_code = int(status_code)
        self.www_authenticate = str(www_authenticate or "").strip()
        self.auth_challenge = parse_www_authenticate(self.www_authenticate)


class MCPRemoteTransportError(MCPTransportError):
    """Raised when remote MCP transport fails structurally."""


@dataclass
class StreamableHTTPSessionState:
    session_id: str = ""

    def request_headers(self) -> dict[str, str]:
        if not self.session_id:
            return {}
        return {"Mcp-Session-Id": self.session_id}

    def capture(self, headers: Any) -> None:
        session_id = str(headers.get("Mcp-Session-Id", "") or "").strip()
        if session_id:
            self.session_id = session_id

    def clear(self) -> None:
        self.session_id = ""


class StdioMCPTransport:
    """Minimal synchronous JSON-RPC-over-stdio transport for MCP."""

    def __init__(self, server: MCPServerConfig) -> None:
        self._server = server
        self._process: subprocess.Popen[bytes] | None = None
        self._selector = selectors.DefaultSelector()
        self._read_buffer = bytearray()
        self._stderr_buffer = bytearray()
        self._stderr_lock = threading.Lock()
        self._stderr_thread: threading.Thread | None = None
        self._stderr_stop = threading.Event()
        self._write_lock = threading.Lock()
        self._next_request_id = 1

    @property
    def server_name(self) -> str:
        return self._server.name

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
        env = os.environ.copy()
        allowlist = set(self._server.stdio_sandbox.env_allowlist)
        try:
            configured_env = resolve_mcp_server_env(self._server)
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

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": str(method or "").strip(),
        }
        if params is not None:
            payload["params"] = params
        self._write_message(payload)

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
            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": str(method or "").strip(),
            }
            if params is not None:
                payload["params"] = params
            self._write_message(payload)
            deadline = time.monotonic() + max(0.1, float(timeout_seconds))
            while True:
                message = self._read_message(deadline=deadline)
                if "method" in message:
                    self._handle_server_message(
                        message=message,
                        server_request_handler=server_request_handler,
                    )
                    continue
                if "id" not in message or message.get("id") != request_id:
                    continue
                return extract_result_message(message=message, method=method)

    def _handle_server_message(
        self,
        *,
        message: dict[str, Any],
        server_request_handler: Any | None,
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
        response_payload = build_server_request_response(
            handler=server_request_handler,
            method=method,
            params=dict(params),
            request_id=request_id,
        )
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
            try:
                self._selector.close()
            except Exception:
                pass
            self._stderr_stop.set()
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=1)
            self._stderr_thread = None
            self._process = None
            self._read_buffer.clear()

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


class StreamableHTTPMCPTransport:
    """Synchronous JSON-over-HTTP MCP transport."""

    def __init__(
        self,
        server: MCPServerConfig,
        *,
        token_store: MCPTokenStore | None = None,
    ) -> None:
        self._server = server
        self._next_request_id = 1
        self._session = StreamableHTTPSessionState()
        self._token_store = token_store
        self._oauth_access_token = str(server.authorization.access_token or "").strip()

    @property
    def server_name(self) -> str:
        return self._server.name

    def is_running(self) -> bool:
        return True

    @property
    def session_state(self) -> StreamableHTTPSessionState:
        return self._session

    def start(self) -> None:
        if not self._server.url:
            raise MCPRemoteTransportError(
                f"MCP server '{self.server_name}' has no remote URL configured.",
                reason_code="mcp_remote_url_missing",
            )

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": str(method or "").strip(),
        }
        if params is not None:
            payload["params"] = params
        self._post_json(
            payload=payload,
            method_name=str(method or "").strip(),
            params=params or {},
            expect_notification_ack=True,
            server_request_handler=None,
        )

    def request(
        self,
        *,
        method: str,
        params: dict[str, Any] | None = None,
        timeout_seconds: float,
        server_request_handler: Any | None = None,
    ) -> dict[str, Any]:
        self.start()
        request_id = self._next_request_id
        self._next_request_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": str(method or "").strip(),
        }
        if params is not None:
            payload["params"] = params
        response = self._post_json(
            payload=payload,
            method_name=str(method or "").strip(),
            params=params or {},
            timeout_seconds=timeout_seconds,
            expect_notification_ack=False,
            server_request_handler=server_request_handler,
            expected_request_id=request_id,
        )
        return extract_result_message(message=response, method=method)

    def close(self) -> None:
        if not self._session.session_id or not self._server.url:
            return
        headers = self._base_headers(protocol_version=MCP_PROTOCOL_VERSION)
        headers.update(self._session.request_headers())
        auth_header = self._authorization_header()
        if auth_header:
            headers["Authorization"] = auth_header
        request = urllib_request.Request(
            url=self._server.url,
            method="DELETE",
            headers=headers,
        )
        try:
            urllib_request.urlopen(
                request,
                timeout=float(self._server.request_timeout_seconds),
            ).close()
        except Exception:
            return
        finally:
            self._session.clear()

    def resume_event_stream(
        self,
        *,
        timeout_seconds: float | None = None,
        last_event_id: str = "",
        server_request_handler: Any | None = None,
    ) -> list[dict[str, Any]]:
        self.start()
        headers = self._base_headers(protocol_version=MCP_PROTOCOL_VERSION)
        headers["Accept"] = "text/event-stream"
        headers.update(self._session.request_headers())
        if last_event_id:
            headers["Last-Event-ID"] = str(last_event_id)
        auth_header = self._authorization_header()
        if auth_header:
            headers["Authorization"] = auth_header
        request = urllib_request.Request(
            url=self._server.url,
            method="GET",
            headers=headers,
        )
        try:
            with urllib_request.urlopen(
                request,
                timeout=float(timeout_seconds or self._server.request_timeout_seconds),
            ) as response:
                self._session.capture(response.headers)
                content_type = str(
                    response.headers.get("Content-Type", "") or ""
                ).strip()
                raw = response.read()
        except urllib_error.HTTPError as exc:
            self._handle_http_error(exc=exc, method_name="resume")
        except urllib_error.URLError as exc:
            raise MCPServerUnavailableError(
                f"MCP server '{self.server_name}' remote endpoint is unavailable.",
                reason_code="mcp_server_unavailable",
            ) from exc
        except TimeoutError as exc:
            raise MCPTimeoutError(
                f"MCP server '{self.server_name}' did not reply before timeout.",
                reason_code="mcp_timeout",
            ) from exc
        if not content_type.startswith("text/event-stream"):
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' returned non-SSE resume stream.",
                reason_code="mcp_http_resume_non_sse",
            )
        messages = parse_sse_messages(raw=raw, server_name=self.server_name)
        for message in messages:
            if "method" not in message:
                continue
            dispatch_server_notification(
                handler=server_request_handler,
                method=str(message.get("method", "") or "").strip(),
                params=dict(message.get("params", {}) or {}),
            )
        return messages

    def _post_json(
        self,
        *,
        payload: dict[str, Any],
        method_name: str,
        params: dict[str, Any],
        timeout_seconds: float | None = None,
        expect_notification_ack: bool,
        server_request_handler: Any | None,
        expected_request_id: Any | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
        headers = self._base_headers(
            protocol_version=protocol_version_from_payload(payload)
        )
        if method_name:
            headers["Mcp-Method"] = method_name
        mcp_name = mcp_name_header(method_name=method_name, params=params)
        if mcp_name:
            headers["Mcp-Name"] = mcp_name
        raw: bytes = b""
        content_type = ""
        refreshed = False
        while True:
            auth_header = self._authorization_header()
            if auth_header:
                headers["Authorization"] = auth_header
            elif "Authorization" in headers:
                del headers["Authorization"]
            headers.update(self._session.request_headers())
            request = urllib_request.Request(
                url=self._server.url,
                method="POST",
                headers=headers,
                data=body,
            )
            try:
                with urllib_request.urlopen(
                    request,
                    timeout=float(
                        timeout_seconds or self._server.request_timeout_seconds
                    ),
                ) as response:
                    status_code = int(
                        getattr(response, "status", response.getcode()) or 0
                    )
                    if expect_notification_ack:
                        if status_code != 202:
                            raise MCPRemoteTransportError(
                                f"MCP server '{self.server_name}' returned HTTP {status_code} for notification {method_name!r}.",
                                reason_code="mcp_notification_http_error",
                            )
                        return {}
                    content_type = str(
                        response.headers.get("Content-Type", "") or ""
                    ).strip()
                    self._session.capture(response.headers)
                    raw = response.read()
                    break
            except urllib_error.HTTPError as exc:
                if (
                    not refreshed
                    and int(getattr(exc, "code", 0) or 0) in {401, 403}
                    and self._refresh_oauth_access_token()
                ):
                    refreshed = True
                    continue
                self._handle_http_error(exc=exc, method_name=method_name)
            except urllib_error.URLError as exc:
                raise MCPServerUnavailableError(
                    f"MCP server '{self.server_name}' remote endpoint is unavailable.",
                    reason_code="mcp_server_unavailable",
                ) from exc
            except TimeoutError as exc:
                raise MCPTimeoutError(
                    f"MCP server '{self.server_name}' did not reply before timeout.",
                    reason_code="mcp_timeout",
                ) from exc
        if not raw and not content_type.startswith("text/event-stream"):
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' returned an empty response body.",
                reason_code="mcp_empty_response_body",
            )
        if content_type.startswith("text/event-stream"):
            messages = parse_sse_messages(raw=raw, server_name=self.server_name)
            final_message: dict[str, Any] | None = None
            for message in messages:
                if "method" in message:
                    request_id = message.get("id")
                    if request_id is None:
                        dispatch_server_notification(
                            handler=server_request_handler,
                            method=str(message.get("method", "") or "").strip(),
                            params=dict(message.get("params", {}) or {}),
                        )
                        continue
                    callback_payload = build_server_request_response(
                        handler=server_request_handler,
                        method=str(message.get("method", "") or "").strip(),
                        params=dict(message.get("params", {}) or {}),
                        request_id=request_id,
                    )
                    self._post_json(
                        payload=callback_payload,
                        method_name="callback-response",
                        params={},
                        timeout_seconds=timeout_seconds,
                        expect_notification_ack=False,
                        server_request_handler=None,
                        expected_request_id=request_id,
                    )
                    continue
                if (
                    expected_request_id is None
                    or message.get("id") == expected_request_id
                ):
                    final_message = message
                    break
            if final_message is None:
                raise MCPProtocolError(
                    f"MCP server '{self.server_name}' did not return a terminal response message.",
                    reason_code="mcp_sse_missing_terminal_response",
                )
            return final_message
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' returned invalid JSON.",
                reason_code="mcp_invalid_json",
            ) from exc
        if not isinstance(decoded, dict):
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' returned a non-object message.",
                reason_code="mcp_non_object_message",
            )
        return decoded

    def _base_headers(self, *, protocol_version: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": protocol_version,
        }

    def _handle_http_error(
        self,
        *,
        exc: urllib_error.HTTPError,
        method_name: str,
    ) -> None:
        status_code = int(getattr(exc, "code", 0) or 0)
        if status_code in {404, 410} and self._session.session_id:
            self._session.clear()
            raise MCPRemoteTransportError(
                f"MCP server '{self.server_name}' rejected the current HTTP session.",
                reason_code="mcp_http_session_invalid",
                details={"status_code": status_code, "method": method_name},
            ) from exc
        if status_code in {401, 403}:
            raise MCPAuthorizationError(
                f"MCP server '{self.server_name}' authorization failed with HTTP {status_code}.",
                status_code=status_code,
                www_authenticate=str(
                    exc.headers.get("WWW-Authenticate", "") if exc.headers else ""
                ),
            ) from exc
        raise MCPRemoteTransportError(
            f"MCP server '{self.server_name}' returned HTTP {status_code} for {method_name!r}.",
            reason_code="mcp_http_error",
            details={"status_code": status_code, "method": method_name},
        ) from exc

    def _authorization_header(self) -> str:
        config = self._server.authorization
        if config.mode == "bearer":
            return f"Bearer {config.bearer_token}"
        if config.mode == "oauth_pkce":
            access_token = self._oauth_access_token or _read_token_ref(
                self._token_store, config.access_token_ref
            )
            if access_token:
                return f"Bearer {access_token}"
        return ""

    def _refresh_oauth_access_token(self) -> bool:
        config = self._server.authorization
        if config.mode != "oauth_pkce":
            return False
        refresh_token = _read_token_ref(self._token_store, config.refresh_token_ref)
        if not refresh_token:
            return False
        metadata = discover_oauth_metadata(
            config, timeout_seconds=float(self._server.request_timeout_seconds)
        )
        token_state = refresh_oauth_access_token(
            config=config,
            metadata=metadata,
            refresh_token=refresh_token,
            timeout_seconds=float(self._server.request_timeout_seconds),
        )
        self._oauth_access_token = token_state.access_token
        if self._token_store is not None and config.access_token_ref:
            self._token_store.set(config.access_token_ref, token_state.access_token)
        if (
            self._token_store is not None
            and config.refresh_token_ref
            and token_state.refresh_token
        ):
            self._token_store.set(config.refresh_token_ref, token_state.refresh_token)
        return True


def _read_token_ref(token_store: MCPTokenStore | None, ref: str) -> str:
    ref = str(ref or "").strip()
    if token_store is None or not ref:
        return ""
    return str(token_store.get(ref) or "").strip()


__all__ = [
    "MCPAuthorizationError",
    "MCPProtocolError",
    "MCPRemoteTransportError",
    "MCPServerUnavailableError",
    "MCPTimeoutError",
    "MCPTransportError",
    "StreamableHTTPSessionState",
    "StreamableHTTPMCPTransport",
    "StdioMCPTransport",
    "parse_www_authenticate",
]
