"""Session route handlers for runs, messages, and events."""

from __future__ import annotations

import re
from http import HTTPStatus
from urllib.parse import parse_qs, unquote

from openminion.api.queries.runs import RunQueryError, list_run_events, list_runs
from openminion.api.queries.sessions import (
    SessionQueryError,
    append_session_event,
    list_session_messages,
)

from .base import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    exception_route_result,
    json_body_required_route_result,
)


_RUNS_RE = re.compile(r"/sessions/([^/]+)/runs")
_RUN_EVENTS_RE = re.compile(r"/sessions/([^/]+)/runs/([^/]+)/events")
_MESSAGES_RE = re.compile(r"/sessions/([^/]+)/messages")
_EVENTS_RE = re.compile(r"/sessions/([^/]+)/events")


def _parse_limit(
    *,
    raw_value: str | None,
    default: int,
    session_id: str,
    run_id: str | None = None,
) -> tuple[int | None, RouteResult | None]:
    if raw_value is None:
        return default, None
    try:
        return int(raw_value), None
    except ValueError:
        return None, error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`limit` must be an integer.",
            details={"query": {"limit": raw_value}},
            retryable=False,
            session_id=session_id,
            run_id=run_id,
        )


def _handle_append_event(
    ctx: APIRouteContext,
    *,
    path: str,
    session_id: str,
    body: dict | None,
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
        status = HTTPStatus.OK
        payload = {"ok": True, **result}
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
    return RouteResult(status=status, payload=payload, session_id=session_id)


def _handle_list_runs(
    ctx: APIRouteContext,
    *,
    session_id: str,
    query: str | None,
) -> RouteResult:
    query_args = parse_qs(query or "", keep_blank_values=False)
    limit, invalid = _parse_limit(
        raw_value=query_args.get("limit", [None])[0],
        default=20,
        session_id=session_id,
    )
    if invalid is not None:
        return invalid
    assert limit is not None
    try:
        runs_payload = list_runs(
            config_path=ctx.config_path,
            session_id=session_id,
            limit=limit,
            runtime=ctx.runtime,
        )
        status = HTTPStatus.OK
        payload = {"ok": True, **runs_payload}
    except RunQueryError as exc:
        return exception_route_result(
            HTTPStatus.NOT_FOUND
            if exc.code in {"session_not_found", "run_not_found"}
            else HTTPStatus.BAD_REQUEST,
            code=exc.code,
            exc=exc,
            details={"session_id": session_id},
            retryable=False,
            session_id=session_id,
        )
    return RouteResult(status=status, payload=payload, session_id=session_id)


def _handle_list_run_events(
    ctx: APIRouteContext,
    *,
    session_id: str,
    run_id: str,
    query: str | None,
) -> RouteResult:
    query_args = parse_qs(query or "", keep_blank_values=False)
    limit, invalid = _parse_limit(
        raw_value=query_args.get("limit", [None])[0],
        default=200,
        session_id=session_id,
        run_id=run_id,
    )
    if invalid is not None:
        return invalid
    assert limit is not None
    try:
        runs_payload = list_run_events(
            config_path=ctx.config_path,
            session_id=session_id,
            run_id=run_id,
            limit=limit,
            runtime=ctx.runtime,
        )
        status = HTTPStatus.OK
        payload = {"ok": True, **runs_payload}
    except RunQueryError as exc:
        return exception_route_result(
            HTTPStatus.NOT_FOUND
            if exc.code in {"session_not_found", "run_not_found"}
            else HTTPStatus.BAD_REQUEST,
            code=exc.code,
            exc=exc,
            details={"session_id": session_id, "run_id": run_id},
            retryable=False,
            session_id=session_id,
            run_id=run_id,
        )
    return RouteResult(
        status=status,
        payload=payload,
        session_id=session_id,
        run_id=run_id,
    )


def _handle_list_session_messages(
    ctx: APIRouteContext,
    *,
    session_id: str,
    query: str | None,
) -> RouteResult:
    query_args = parse_qs(query or "", keep_blank_values=False)
    limit, invalid = _parse_limit(
        raw_value=query_args.get("limit", [None])[0],
        default=100,
        session_id=session_id,
    )
    if invalid is not None:
        return invalid
    assert limit is not None
    try:
        session_payload = list_session_messages(
            config_path=ctx.config_path,
            session_id=session_id,
            limit=limit,
            runtime=ctx.runtime,
        )
        status = HTTPStatus.OK
        payload = {"ok": True, **session_payload}
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
    return RouteResult(status=status, payload=payload, session_id=session_id)


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict | None,
    query: str | None,
) -> RouteResult | None:
    if (
        method_name == "POST"
        and (events_route := _EVENTS_RE.fullmatch(path)) is not None
    ):
        return _handle_append_event(
            ctx,
            path=path,
            session_id=unquote(events_route.group(1)),
            body=body,
        )
    if method_name == "GET" and (runs_route := _RUNS_RE.fullmatch(path)) is not None:
        return _handle_list_runs(
            ctx,
            session_id=unquote(runs_route.group(1)),
            query=query,
        )

    if (
        method_name == "GET"
        and (run_events_route := _RUN_EVENTS_RE.fullmatch(path)) is not None
    ):
        return _handle_list_run_events(
            ctx,
            session_id=unquote(run_events_route.group(1)),
            run_id=unquote(run_events_route.group(2)),
            query=query,
        )

    if (
        method_name == "GET"
        and (session_route := _MESSAGES_RE.fullmatch(path)) is not None
    ):
        return _handle_list_session_messages(
            ctx,
            session_id=unquote(session_route.group(1)),
            query=query,
        )

    return None
