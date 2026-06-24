"""MCP transport protocol helpers."""

import json
from typing import Any

from .contracts import MCP_PROTOCOL_VERSION


def _protocol_error(*args: Any, **kwargs: Any) -> Exception:
    from .transport import MCPProtocolError

    return MCPProtocolError(*args, **kwargs)


def parse_www_authenticate(value: str) -> dict[str, str]:
    raw = str(value or "").strip()
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
        raise _protocol_error(
            str(
                error.get("message")
                or error.get("code")
                or f"MCP method {method!r} failed."
            ).strip(),
            reason_code=str(error.get("reason_code", "") or "").strip(),
        )
    result = message.get("result")
    if not isinstance(result, dict):
        raise _protocol_error(
            f"MCP method {method!r} returned a non-object result.",
            reason_code="mcp_non_object_result",
        )
    return dict(result)


def parse_sse_messages(*, raw: bytes, server_name: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _protocol_error(
            f"MCP server '{server_name}' returned non-UTF-8 SSE data.",
            reason_code="mcp_sse_invalid_encoding",
        ) from exc
    messages: list[dict[str, Any]] = []
    sse_kind = "message"
    data_lines: list[str] = []
    terminated = False

    def _finalize_event() -> None:
        nonlocal sse_kind, data_lines, terminated
        if not data_lines and sse_kind != "end":
            sse_kind = "message"
            data_lines = []
            return
        if sse_kind == "end":
            terminated = True
            sse_kind = "message"
            data_lines = []
            return
        payload = "\n".join(data_lines).strip()
        sse_kind = "message"
        data_lines = []
        if not payload:
            return
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise _protocol_error(
                f"MCP server '{server_name}' returned malformed SSE JSON.",
                reason_code="mcp_sse_parse_error",
            ) from exc
        if not isinstance(decoded, dict):
            raise _protocol_error(
                f"MCP server '{server_name}' returned a non-object SSE message.",
                reason_code="mcp_sse_parse_error",
            )
        messages.append(decoded)

    for raw_line in text.splitlines():
        if terminated:
            break
        line = raw_line.rstrip("\r")
        if not line:
            _finalize_event()
            continue
        if line.startswith(":"):
            continue
        if ":" in line:
            field, value = line.split(":", 1)
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = line, ""
        field = field.strip().lower()
        if field == "event":
            sse_kind = value.strip().lower() or "message"
            continue
        if field == "data":
            data_lines.append(value)
            continue
        raise _protocol_error(
            f"MCP server '{server_name}' returned malformed SSE field {field!r}.",
            reason_code="mcp_sse_parse_error",
        )
    if not terminated:
        _finalize_event()
    return messages


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
    if handler is None:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32601,
                "message": f"Unsupported MCP client method: {method}",
            },
        }
    request_handler = getattr(handler, "handle_request", None)
    if callable(request_handler):
        call = request_handler
    elif callable(handler):
        call = handler
    else:
        call = None
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
    if method_name == "tools/call":
        return str(params.get("name", "") or "").strip()
    if method_name == "resources/read":
        return str(params.get("uri", "") or "").strip()
    if method_name == "prompts/get":
        return str(params.get("name", "") or "").strip()
    return ""
