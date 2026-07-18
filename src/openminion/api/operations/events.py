"""Route support for appending session events."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from openminion.api.queries.sessions import SessionQueryError, append_session_event
from openminion.api.routes.contracts import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    exception_route_result,
    json_body_required_route_result,
)


def handle_append_session_event(
    ctx: APIRouteContext,
    *,
    path: str,
    session_id: str,
    body: dict[str, Any] | None,
) -> RouteResult:
    if body is None:
        return json_body_required_route_result(path=path, session_id=session_id)
    event_type = str(body.get("event_type", "")).strip()
    if not event_type:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`event_type` is required.",
            details={"path": path},
            retryable=False,
            session_id=session_id,
        )
    payload_value = body.get("payload")
    if payload_value is None:
        event_payload = {}
    elif isinstance(payload_value, dict):
        event_payload = payload_value
    else:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`payload` must be an object.",
            details={"path": path},
            retryable=False,
            session_id=session_id,
        )
    try:
        result = append_session_event(
            config_path=ctx.config_path,
            session_id=session_id,
            event_type=event_type,
            payload=event_payload,
            runtime=ctx.runtime,
        )
    except SessionQueryError as exc:
        return exception_route_result(
            HTTPStatus.NOT_FOUND
            if exc.code == "session_not_found"
            else HTTPStatus.BAD_REQUEST,
            code=exc.code,
            exc=exc,
            details={"session_id": session_id},
            retryable=False,
            session_id=session_id,
        )
    return RouteResult(
        status=HTTPStatus.OK,
        payload={"ok": True, **result},
        session_id=session_id,
    )


__all__ = ["handle_append_session_event"]
