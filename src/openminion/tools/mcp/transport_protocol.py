"""MCP transport protocol helpers."""

import base64
import json
from collections.abc import Iterable, Iterator
from typing import Any, Callable, Never

from .contracts import MCP_PROTOCOL_VERSION
from .errors import MCPProtocolError


_SUBSCRIPTION_NOTIFICATION_METHODS = {
    "toolsListChanged": "notifications/tools/list_changed",
    "promptsListChanged": "notifications/prompts/list_changed",
    "resourcesListChanged": "notifications/resources/list_changed",
}
_RESOURCE_SUBSCRIPTIONS = "resourceSubscriptions"


class MCPSubscriptionValidator:
    """Validate one modern subscription stream and its event correlation."""

    def __init__(
        self, *, server_name: str, request_id: Any, notifications: dict[str, Any]
    ) -> None:
        self._server_name = server_name
        self._request_id = request_id
        valid_names = set(_SUBSCRIPTION_NOTIFICATION_METHODS) | {
            _RESOURCE_SUBSCRIPTIONS
        }
        invalid = set(notifications) - valid_names
        if invalid:
            self._fail(f"requested unsupported notifications {sorted(invalid)!r}")
        self._requested = {
            name
            for name, enabled in notifications.items()
            if enabled is True and name in _SUBSCRIPTION_NOTIFICATION_METHODS
        }
        for name in _SUBSCRIPTION_NOTIFICATION_METHODS:
            if name in notifications and not isinstance(notifications[name], bool):
                self._fail(f"requested invalid notification flag {name!r}")
        self._requested_resources = _string_list(
            notifications.get(_RESOURCE_SUBSCRIPTIONS, []),
            fail=self._fail,
        )
        self._accepted: dict[str, Any] | None = None
        self._event_count = 0

    def accept(self, message: dict[str, Any]) -> bool:
        method = str(message.get("method", "") or "").strip()
        params = dict(message.get("params", {}) or {})
        subscription_id = dict(params.get("_meta", {}) or {}).get(
            "io.modelcontextprotocol/subscriptionId"
        )
        if method == "notifications/subscriptions/acknowledged":
            if self._accepted is not None:
                self._fail("acknowledged the subscription more than once")
            if subscription_id != self._request_id:
                self._fail("returned a mismatched subscription id")
            raw_accepted = params.get("notifications", {})
            if not isinstance(raw_accepted, dict):
                self._fail("returned invalid acknowledged notifications")
            accepted = dict(raw_accepted)
            for name, enabled in accepted.items():
                if name == _RESOURCE_SUBSCRIPTIONS:
                    resources = _string_list(enabled, fail=self._fail)
                    if not set(resources).issubset(self._requested_resources):
                        self._fail("acknowledged resources that were not requested")
                    accepted[name] = resources
                    continue
                if enabled is not True or name not in self._requested:
                    self._fail("acknowledged notifications that were not requested")
            self._accepted = accepted
            return False
        if self._accepted is None:
            self._fail("did not acknowledge the subscription first")
        if subscription_id != self._request_id:
            self._fail("returned an uncorrelated subscription event")
        allowed_methods = {
            _SUBSCRIPTION_NOTIFICATION_METHODS[name]
            for name, enabled in self._accepted.items()
            if name in _SUBSCRIPTION_NOTIFICATION_METHODS and enabled is True
        }
        if method == "notifications/resources/updated":
            uri = str(params.get("uri", "") or "").strip()
            accepted_resources = set(self._accepted.get(_RESOURCE_SUBSCRIPTIONS, []))
            if not uri or uri not in accepted_resources:
                self._fail("sent an update for an unaccepted resource")
        elif method not in allowed_methods:
            self._fail(f"sent unacknowledged notification {method!r}")
        self._event_count += 1
        return True

    def finish(self, message: dict[str, Any]) -> dict[str, Any]:
        if self._accepted is None:
            self._fail("did not acknowledge the subscription first")
        if message.get("id") != self._request_id:
            self._fail("returned a mismatched terminal response")
        result = extract_result_message(message=message, method="subscriptions/listen")
        result.update(
            {
                "subscriptionId": self._request_id,
                "notifications": dict(self._accepted),
                "eventCount": self._event_count,
                "closed": "graceful",
            }
        )
        return result

    def _fail(self, detail: str) -> Never:
        raise MCPProtocolError(
            f"MCP server '{self._server_name}' {detail}.",
            reason_code="mcp_subscription_protocol_error",
        )


def _string_list(value: Any, *, fail: Callable[[str], Never]) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        fail("used invalid resource subscriptions")
    return [item.strip() for item in value]


def parse_www_authenticate(value: str) -> dict[str, str]:
    raw = value.strip()
    if not raw:
        return {}
    scheme, _, rest = raw.partition(" ")
    challenge: dict[str, str] = {"scheme": scheme.strip()}
    for item in rest.split(","):
        key, sep, val = item.strip().partition("=")
        if not sep or not key:
            continue
        challenge[key.strip()] = val.strip().strip('"')
    return challenge


def extract_result_message(*, message: dict[str, Any], method: str) -> dict[str, Any]:
    error = message.get("error")
    if isinstance(error, dict):
        details = {"code": error.get("code")}
        data = error.get("data")
        if isinstance(data, dict):
            details["data"] = dict(data)
        raise MCPProtocolError(
            str(
                error.get("message")
                or error.get("code")
                or f"MCP method {method!r} failed."
            ).strip(),
            reason_code=str(error.get("reason_code", "") or "").strip(),
            details=details,
        )
    result = message.get("result")
    if not isinstance(result, dict):
        raise MCPProtocolError(
            f"MCP method {method!r} returned a non-object result.",
            reason_code="mcp_non_object_result",
        )
    return dict(result)


def parse_sse_messages(*, raw: bytes, server_name: str) -> list[dict[str, Any]]:
    return list(iter_sse_messages(lines=raw.splitlines(), server_name=server_name))


def iter_sse_messages(
    *, lines: Iterable[bytes], server_name: str
) -> Iterator[dict[str, Any]]:
    """Yield JSON-RPC messages as complete SSE events arrive."""

    sse_kind = "message"
    data_lines: list[str] = []

    def decode_event() -> dict[str, Any] | None:
        nonlocal sse_kind, data_lines
        if sse_kind == "end":
            return None
        payload = "\n".join(data_lines).strip()
        sse_kind = "message"
        data_lines = []
        if not payload:
            return None
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MCPProtocolError(
                f"MCP server '{server_name}' returned malformed SSE JSON.",
                reason_code="mcp_sse_parse_error",
            ) from exc
        if not isinstance(decoded, dict):
            raise MCPProtocolError(
                f"MCP server '{server_name}' returned a non-object SSE message.",
                reason_code="mcp_sse_parse_error",
            )
        return decoded

    for raw_line in lines:
        try:
            line = raw_line.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError as exc:
            raise MCPProtocolError(
                f"MCP server '{server_name}' returned non-UTF-8 SSE data.",
                reason_code="mcp_sse_invalid_encoding",
            ) from exc
        if not line:
            if sse_kind == "end":
                return
            message = decode_event()
            if message is not None:
                yield message
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        field = field.strip().lower()
        if field == "event":
            sse_kind = value.strip().lower() or "message"
        elif field == "data":
            data_lines.append(value)
        else:
            continue
    if sse_kind != "end":
        message = decode_event()
        if message is not None:
            yield message


def dispatch_server_notification(
    *,
    handler: Any | None,
    method: str,
    params: dict[str, Any],
) -> None:
    if handler is None:
        return
    notification_handler = getattr(handler, "handle_notification", None)
    if callable(notification_handler):
        notification_handler(method=method, params=dict(params))
        return
    if callable(handler):
        handler(method=method, params=dict(params))


def build_server_request_response(
    *,
    handler: Any | None,
    method: str,
    params: dict[str, Any],
    request_id: Any,
) -> dict[str, Any]:
    call = None
    if handler is not None:
        request_handler = getattr(handler, "handle_request", None)
        if callable(request_handler):
            call = request_handler
        elif callable(handler):
            call = handler
    if call is None:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32601,
                "message": f"Unsupported MCP client method: {method}",
            },
        }
    try:
        result = call(method=method, params=dict(params)) or {}
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32000,
                "message": str(exc or exc.__class__.__name__),
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result if isinstance(result, dict) else {},
    }


def protocol_version_from_payload(payload: dict[str, Any]) -> str:
    params = payload.get("params", {})
    if isinstance(params, dict):
        meta = params.get("_meta", {})
        if isinstance(meta, dict):
            protocol = str(
                meta.get("io.modelcontextprotocol/protocolVersion", "") or ""
            ).strip()
            if protocol:
                return protocol
        protocol = str(params.get("protocolVersion", "") or "").strip()
        if protocol:
            return protocol
    return MCP_PROTOCOL_VERSION


def mcp_name_header(*, method_name: str, params: dict[str, Any]) -> str:
    value = ""
    if method_name == "tools/call":
        value = str(params.get("name", "") or "")
    elif method_name == "resources/read":
        value = str(params.get("uri", "") or "")
    elif method_name == "prompts/get":
        value = str(params.get("name", "") or "")
    elif method_name.startswith("tasks/"):
        value = str(params.get("taskId", "") or "")
    return encode_mcp_header_value(value) if value else ""


def encode_mcp_header_value(value: Any) -> str:
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, int):
        if abs(value) > (2**53 - 1):
            raise ValueError("MCP integer header value exceeds JavaScript safe range")
        rendered = str(value)
    elif isinstance(value, str):
        rendered = value
    else:
        raise ValueError("MCP header values must be string, integer, or boolean")
    sentinel = rendered.startswith("=?base64?") and rendered.endswith("?=")
    safe_ascii = all(0x20 <= ord(char) <= 0x7E for char in rendered)
    if safe_ascii and rendered == rendered.strip() and not sentinel:
        return rendered
    encoded = base64.b64encode(rendered.encode("utf-8")).decode("ascii")
    return f"=?base64?{encoded}?="
