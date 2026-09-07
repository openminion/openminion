"""Route support for reading and appending session events."""

from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs

from openminion.api.queries.sessions import (
    SessionQueryError,
    append_session_event,
    list_session_events,
)
from openminion.api.routes.contracts import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    exception_route_result,
    json_body_required_route_result,
)


def handle_list_session_events(
    ctx: APIRouteContext,
    *,
    session_id: str,
    query: str | None,
) -> RouteResult:
    query_args = parse_qs(query or "", keep_blank_values=False)
    try:
        after_id = int(query_args.get("after_id", ["0"])[0])
        limit = int(query_args.get("limit", ["100"])[0])
    except ValueError:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`after_id` and `limit` must be integers.",
            details={"query": query_args},
            retryable=False,
            session_id=session_id,
        )
    try:
        payload = list_session_events(
            config_path=ctx.config_path,
            session_id=session_id,
            after_id=after_id,
            limit=limit,
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
        payload={"ok": True, **payload},
        session_id=session_id,
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


__all__ = ["handle_append_session_event", "handle_list_session_events"]
