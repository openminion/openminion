from __future__ import annotations

from http import HTTPStatus
from typing import Any

from openminion.api.operations.a2a import (
    A2A_NETWORK_TOKEN_ENV,
    AGENT_CARD_WELL_KNOWN_PATH,
    authorize_a2a_request,
    build_agent_card_payload,
    handle_jsonrpc,
    record_auth_denial,
)
from openminion.api.routes.contracts import (
    APIRouteContext,
    RouteResult,
    error_route_result,
)

_A2A_JSONRPC_PATH = "/a2a/v1/jsonrpc"
_A2A_TASK_EVENTS_PREFIX = "/a2a/v1/tasks/"


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    del query
    if method_name == "GET" and path == AGENT_CARD_WELL_KNOWN_PATH:
        return RouteResult(HTTPStatus.OK, build_agent_card_payload())
    if method_name == "POST" and path == _A2A_JSONRPC_PATH:
        auth_result = authorize_a2a_request(ctx.request_headers)
        if auth_result is not None:
            record_auth_denial(ctx, path=path, reason=_route_error_code(auth_result))
            return auth_result
        return handle_jsonrpc(ctx, body or {})
    if method_name == "GET" and _is_task_events_path(path):
        auth_result = authorize_a2a_request(ctx.request_headers)
        if auth_result is not None:
            record_auth_denial(ctx, path=path, reason=_route_error_code(auth_result))
            return auth_result
        return _streaming_not_supported(path)
    return None


def _streaming_not_supported(path: str) -> RouteResult:
    return error_route_result(
        HTTPStatus.NOT_IMPLEMENTED,
        code="a2a_streaming_not_supported",
        message="External A2A task streaming is not enabled in v1.",
        details={"path": path, "route_prefix": "/a2a/v1"},
        retryable=False,
    )


def _is_task_events_path(path: str) -> bool:
    return path.startswith(_A2A_TASK_EVENTS_PREFIX) and path.endswith("/events")


def _route_error_code(result: RouteResult) -> str:
    payload = result.payload
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("code") or "auth_denied")
    return "auth_denied"


__all__ = ["A2A_NETWORK_TOKEN_ENV", "handle_request"]
