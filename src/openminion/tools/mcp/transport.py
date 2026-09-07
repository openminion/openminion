"""MCP client transport."""

import json
import threading
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

from openminion.base.config.mcp import MCPServerConfig

from .auth import (
    MCPOAuthMetadata,
    MCPTokenStore,
    discover_oauth_metadata,
    read_issuer_bound_token,
    refresh_oauth_access_token,
    read_token_ref,
    store_issuer_bound_token,
)
from .contracts import (
    MCP_MODERN_PROTOCOL_ERROR_CODES,
    MCP_MODERN_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION,
)
from .schemas import MCPHeaderBinding
from .errors import (
    MCPProtocolError,
    MCPRemoteTransportError,
    MCPServerUnavailableError,
    MCPTimeoutError,
    MCPTransportError,
)
from .transport_protocol import build_server_request_response
from .transport_protocol import dispatch_server_notification
from .transport_protocol import encode_mcp_header_value
from .transport_protocol import extract_result_message
from .transport_protocol import iter_sse_messages
from .transport_protocol import mcp_name_header
from .transport_protocol import parse_sse_messages
from .transport_protocol import parse_www_authenticate
from .transport_protocol import protocol_version_from_payload


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


class StreamableHTTPMCPTransport:
    """Synchronous JSON-over-HTTP MCP transport."""

    def __init__(
        self,
        server: MCPServerConfig,
        *,
        token_store: MCPTokenStore | None = None,
        auth_change_handler: Callable[[], None] | None = None,
    ) -> None:
        self._server = server
        self._next_request_id = 1
        self._session = StreamableHTTPSessionState()
        self._token_store = token_store
        self._auth_change_handler = auth_change_handler
        self._oauth_access_token = str(server.authorization.access_token or "").strip()
        self._oauth_metadata: MCPOAuthMetadata | None = None
        self._tool_header_bindings: dict[str, tuple[MCPHeaderBinding, ...]] = {}
        self._active_responses: dict[int, Any] = {}
        self._cancelled_requests: set[int] = set()
        self._state_lock = threading.RLock()

    @property
    def server_name(self) -> str:
        return self._server.name

    def is_running(self) -> bool:
        return True

    def stderr_tail(self, *, limit: int = 4096) -> str:
        del limit
        return ""

    @property
    def session_state(self) -> StreamableHTTPSessionState:
        return self._session

    @property
    def authorization_identity(self) -> str:
        authorization = self._server.authorization
        return str(
            authorization.access_token_ref
            or authorization.client_id
            or authorization.mode
        )

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

    def set_tool_header_bindings(
        self, tool_name: str, bindings: tuple[MCPHeaderBinding, ...]
    ) -> None:
        self._tool_header_bindings[tool_name] = bindings

    def cancel_request(self, request_id: int) -> None:
        with self._state_lock:
            response = self._active_responses.get(request_id)
            if response is None:
                return
            self._cancelled_requests.add(request_id)
            response.close()

    def request(
        self,
        *,
        method: str,
        params: dict[str, Any] | None = None,
        timeout_seconds: float,
        server_request_handler: Any | None = None,
    ) -> dict[str, Any]:
        self.start()
        with self._state_lock:
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
        with self._state_lock:
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
        except (urllib_error.HTTPError, urllib_error.URLError, TimeoutError):
            return
        finally:
            with self._state_lock:
                self._session.clear()

    def resume_event_stream(
        self,
        *,
        timeout_seconds: float | None = None,
        last_event_id: str = "",
        server_request_handler: Any | None = None,
    ) -> list[dict[str, Any]]:
        self.start()
        with self._state_lock:
            if not self._session.session_id:
                raise MCPRemoteTransportError(
                    "Standalone HTTP event streams are only available for legacy MCP sessions.",
                    reason_code="mcp_http_legacy_stream_only",
                )
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
                with self._state_lock:
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
        return self._send_http_payload(
            payload=payload,
            method_name=method_name,
            params=params,
            timeout_seconds=timeout_seconds,
            expect_notification_ack=expect_notification_ack,
            server_request_handler=server_request_handler,
            expected_request_id=expected_request_id,
        ) or {}

    def _send_http_payload(
        self,
        *,
        payload: dict[str, Any],
        method_name: str,
        params: dict[str, Any],
        timeout_seconds: float | None,
        expect_notification_ack: bool,
        server_request_handler: Any | None,
        expected_request_id: Any | None,
    ) -> dict[str, Any] | None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
        protocol_version = protocol_version_from_payload(payload)
        headers, use_session = self._post_headers(
            protocol_version=protocol_version,
            method_name=method_name,
            params=params,
        )
        refreshed = False
        while True:
            with self._state_lock:
                auth_header = self._authorization_header()
                if auth_header:
                    headers["Authorization"] = auth_header
                else:
                    headers.pop("Authorization", None)
                if use_session:
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
                    return self._consume_http_response(
                        response,
                        payload=payload,
                        method_name=method_name,
                        use_session=use_session,
                        timeout_seconds=timeout_seconds,
                        expect_notification_ack=expect_notification_ack,
                        server_request_handler=server_request_handler,
                        expected_request_id=expected_request_id,
                    )
            except urllib_error.HTTPError as exc:
                authenticate = str(
                    exc.headers.get("WWW-Authenticate", "") if exc.headers else ""
                )
                challenge = parse_www_authenticate(authenticate)
                if (
                    not refreshed
                    and int(getattr(exc, "code", 0) or 0) in {401, 403}
                    and self._refresh_oauth_access_token(
                        resource_metadata_url=challenge.get("resource_metadata", "")
                    )
                ):
                    if self._auth_change_handler is not None:
                        self._auth_change_handler()
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

    def _post_headers(
        self,
        *,
        protocol_version: str,
        method_name: str,
        params: dict[str, Any],
    ) -> tuple[dict[str, str], bool]:
        headers = self._base_headers(protocol_version=protocol_version)
        use_session = protocol_version != MCP_MODERN_PROTOCOL_VERSION
        with self._state_lock:
            if not use_session:
                self._session.clear()
        if method_name:
            headers["Mcp-Method"] = method_name
        mcp_name = mcp_name_header(method_name=method_name, params=params)
        if mcp_name:
            headers["Mcp-Name"] = mcp_name
        if protocol_version != MCP_MODERN_PROTOCOL_VERSION or method_name != "tools/call":
            return headers, use_session
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return headers, use_session
        tool_name = str(params.get("name", "") or "")
        for binding in self._tool_header_bindings.get(tool_name, ()):
            value = _value_at_path(arguments, binding.path)
            if value is not None:
                headers[f"Mcp-Param-{binding.header_name}"] = encode_mcp_header_value(value)
        return headers, use_session

    def _consume_http_response(
        self,
        response: Any,
        *,
        payload: dict[str, Any],
        method_name: str,
        use_session: bool,
        timeout_seconds: float | None,
        expect_notification_ack: bool,
        server_request_handler: Any | None,
        expected_request_id: Any | None,
    ) -> dict[str, Any] | None:
        status_code = int(getattr(response, "status", response.getcode()) or 0)
        if expect_notification_ack:
            if status_code != 202:
                raise MCPRemoteTransportError(
                    f"MCP server '{self.server_name}' returned HTTP {status_code} for notification {method_name!r}.",
                    reason_code="mcp_notification_http_error",
                )
            return None
        content_type = str(response.headers.get("Content-Type", "") or "").strip()
        if use_session:
            with self._state_lock:
                self._session.capture(response.headers)
        request_id = payload.get("id")
        if content_type.startswith("text/event-stream") and isinstance(request_id, int):
            with self._state_lock:
                self._active_responses[request_id] = response
        try:
            if (
                method_name == "subscriptions/listen"
                and content_type.startswith("text/event-stream")
            ):
                return self._decode_subscription_messages(
                    messages=iter_sse_messages(
                        lines=response,
                        server_name=self.server_name,
                    ),
                    expected_request_id=expected_request_id,
                    server_request_handler=server_request_handler,
                )
            raw = response.read()
            with self._state_lock:
                cancelled = request_id in self._cancelled_requests
            if cancelled:
                raise MCPProtocolError(
                    f"MCP request {request_id} was cancelled.",
                    reason_code="mcp_request_cancelled",
                )
        except (AttributeError, OSError, ValueError) as exc:
            with self._state_lock:
                cancelled = request_id in self._cancelled_requests
            if cancelled:
                raise MCPProtocolError(
                    f"MCP request {request_id} was cancelled.",
                    reason_code="mcp_request_cancelled",
                ) from exc
            raise
        finally:
            if isinstance(request_id, int):
                with self._state_lock:
                    self._active_responses.pop(request_id, None)
                    self._cancelled_requests.discard(request_id)
        return self._decode_post_response(
            raw=raw,
            content_type=content_type,
            timeout_seconds=timeout_seconds,
            server_request_handler=server_request_handler,
            expected_request_id=expected_request_id,
            protocol_version=protocol_version_from_payload(payload),
        )

    def _decode_post_response(
        self,
        *,
        raw: bytes,
        content_type: str,
        timeout_seconds: float | None,
        server_request_handler: Any | None,
        expected_request_id: Any | None,
        protocol_version: str,
    ) -> dict[str, Any]:
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
                    if protocol_version == MCP_MODERN_PROTOCOL_VERSION:
                        raise MCPProtocolError(
                            f"MCP server '{self.server_name}' sent a server request on a modern HTTP stream.",
                            reason_code="mcp_modern_http_server_request",
                        )
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

    def _decode_subscription_messages(
        self,
        *,
        messages: Any,
        expected_request_id: Any,
        server_request_handler: Any | None,
    ) -> dict[str, Any]:
        stream = iter(messages)
        acknowledged_message = next(stream, None)
        if acknowledged_message is None or acknowledged_message.get("method") != (
            "notifications/subscriptions/acknowledged"
        ):
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' did not acknowledge the subscription first.",
                reason_code="mcp_subscription_ack_missing",
            )
        acknowledged = dict(acknowledged_message.get("params", {}) or {})
        subscription_id = dict(acknowledged.get("_meta", {}) or {}).get(
            "io.modelcontextprotocol/subscriptionId"
        )
        if subscription_id != expected_request_id:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' returned a mismatched subscription id.",
                reason_code="mcp_subscription_id_mismatch",
            )
        event_count = 0
        final_message: dict[str, Any] | None = None
        for message in stream:
            method = str(message.get("method", "") or "").strip()
            if not method:
                if message.get("id") == expected_request_id:
                    final_message = message
                    break
                continue
            params = dict(message.get("params", {}) or {})
            event_id = dict(params.get("_meta", {}) or {}).get(
                "io.modelcontextprotocol/subscriptionId"
            )
            if event_id != subscription_id:
                raise MCPProtocolError(
                    f"MCP server '{self.server_name}' returned an uncorrelated subscription event.",
                    reason_code="mcp_subscription_id_mismatch",
                )
            dispatch_server_notification(
                handler=server_request_handler,
                method=method,
                params=params,
            )
            event_count += 1
        if final_message is None:
            raise MCPProtocolError(
                f"MCP server '{self.server_name}' closed the subscription unexpectedly.",
                reason_code="mcp_subscription_lost",
            )
        result = dict(final_message.get("result", {}) or {})
        result.update(
            {
                "subscriptionId": subscription_id,
                "notifications": dict(acknowledged.get("notifications", {}) or {}),
                "eventCount": event_count,
                "closed": "graceful",
            }
        )
        return {"jsonrpc": "2.0", "id": expected_request_id, "result": result}

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
        raw = exc.read()
        decoded: dict[str, Any] | None = None
        if raw:
            try:
                candidate = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                candidate = None
            if isinstance(candidate, dict):
                decoded = candidate
        error = decoded.get("error") if decoded else None
        code = error.get("code") if isinstance(error, dict) else None
        if status_code in {400, 404, 405} and code in MCP_MODERN_PROTOCOL_ERROR_CODES:
            extract_result_message(message=decoded or {}, method=method_name)
        if status_code in {404, 410} and self._session.session_id:
            self._session.clear()
            raise MCPRemoteTransportError(
                f"MCP server '{self.server_name}' rejected the current HTTP session.",
                reason_code="mcp_http_session_invalid",
                details={"status_code": status_code, "method": method_name},
            ) from exc
        if status_code in {401, 403}:
            authenticate = str(
                exc.headers.get("WWW-Authenticate", "") if exc.headers else ""
            )
            challenge = parse_www_authenticate(authenticate)
            reason_code = (
                "mcp_authorization_scope_insufficient"
                if status_code == 403 and challenge.get("error") == "insufficient_scope"
                else "mcp_authorization_error"
            )
            raise MCPAuthorizationError(
                f"MCP server '{self.server_name}' authorization failed with HTTP {status_code}.",
                status_code=status_code,
                www_authenticate=authenticate,
                reason_code=reason_code,
            ) from exc
        if status_code in {400, 404, 405} and code not in MCP_MODERN_PROTOCOL_ERROR_CODES:
            raise MCPRemoteTransportError(
                f"MCP server '{self.server_name}' does not expose the modern HTTP protocol.",
                reason_code="mcp_http_legacy_candidate",
                details={"status_code": status_code, "method": method_name},
            ) from exc
        raise MCPRemoteTransportError(
            f"MCP server '{self.server_name}' returned HTTP {status_code} for {method_name!r}.",
            reason_code="mcp_http_error",
            details={
                "status_code": status_code,
                "method": method_name,
                "code": code,
            },
        ) from exc

    def _authorization_header(self) -> str:
        config = self._server.authorization
        if config.mode == "bearer":
            token = config.bearer_token or read_token_ref(
                self._token_store, config.bearer_token_ref
            )
            if not token:
                raise MCPAuthorizationError(
                    f"MCP server '{self.server_name}' bearer token reference is unavailable.",
                    reason_code="mcp_bearer_token_missing",
                )
            return f"Bearer {token}"
        if config.mode == "oauth_pkce":
            access_token = self._oauth_access_token
            if not access_token and config.access_token_ref:
                try:
                    metadata = self._oauth_metadata_for_server()
                    access_token = read_issuer_bound_token(
                        self._token_store,
                        config.access_token_ref,
                        metadata.issuer,
                    )
                except ValueError as exc:
                    raise MCPAuthorizationError(
                        f"MCP server '{self.server_name}' stored access token is not valid for the selected issuer.",
                        reason_code="mcp_oauth_token_issuer_mismatch",
                    ) from exc
            if access_token:
                return f"Bearer {access_token}"
        return ""

    def _oauth_metadata_for_server(
        self, *, resource_metadata_url: str = ""
    ) -> MCPOAuthMetadata:
        if resource_metadata_url or self._oauth_metadata is None:
            self._oauth_metadata = discover_oauth_metadata(
                self._server.authorization,
                resource=self._server.url,
                resource_metadata_url=resource_metadata_url,
                timeout_seconds=float(self._server.request_timeout_seconds),
            )
        return self._oauth_metadata

    def _refresh_oauth_access_token(self, *, resource_metadata_url: str = "") -> bool:
        with self._state_lock:
            config = self._server.authorization
            if config.mode != "oauth_pkce":
                return False
            metadata = self._oauth_metadata_for_server(
                resource_metadata_url=resource_metadata_url
            )
            try:
                refresh_token = read_issuer_bound_token(
                    self._token_store,
                    config.refresh_token_ref,
                    metadata.issuer,
                )
            except ValueError as exc:
                raise MCPAuthorizationError(
                    f"MCP server '{self.server_name}' stored refresh token is not valid for the selected issuer.",
                    reason_code="mcp_oauth_token_issuer_mismatch",
                ) from exc
            if not refresh_token:
                return False
            token_state = refresh_oauth_access_token(
                config=config,
                metadata=metadata,
                refresh_token=refresh_token,
                resource=self._server.url,
                timeout_seconds=float(self._server.request_timeout_seconds),
            )
            self._oauth_access_token = token_state.access_token
            if self._token_store is not None and config.access_token_ref:
                store_issuer_bound_token(
                    self._token_store,
                    config.access_token_ref,
                    token_state.access_token,
                    metadata.issuer,
                )
            if (
                self._token_store is not None
                and config.refresh_token_ref
                and token_state.refresh_token
            ):
                store_issuer_bound_token(
                    self._token_store,
                    config.refresh_token_ref,
                    token_state.refresh_token,
                    metadata.issuer,
                )
            return True


def _value_at_path(value: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = value
    for segment in path:
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


from .stdio_transport import StdioMCPTransport  # noqa: E402


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
