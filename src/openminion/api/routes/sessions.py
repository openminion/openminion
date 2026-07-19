"""Session route handlers for runs, messages, and events."""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, unquote

from openminion.api.operations.context_traces import maybe_handle_context_traces_request
from openminion.api.operations.session_shares import maybe_handle_session_shares_request
from openminion.api.operations.events import handle_append_session_event
from openminion.api.operations.session_continuations import (
    handle_apply_continuation,
    handle_build_continuation,
)
from openminion.api.queries.runs import RunQueryError, list_run_events, list_runs
from openminion.api.queries.sessions import (
    SessionQueryError,
    list_session_messages,
)
from .contracts import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    exception_route_result,
)


_RUNS_RE = re.compile(r"/sessions/([^/]+)/runs")
_RUN_EVENTS_RE = re.compile(r"/sessions/([^/]+)/runs/([^/]+)/events")
_MESSAGES_RE = re.compile(r"/sessions/([^/]+)/messages")
_EVENTS_RE = re.compile(r"/sessions/([^/]+)/events")
_CONTINUATIONS_RE = re.compile(r"(?:/v1)?/sessions/([^/]+)/continuations")
_CONTINUATION_APPLY_RE = re.compile(
    r"(?:/v1)?/sessions/([^/]+)/continuations/([^/]+)/apply"
)

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
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    share_result = maybe_handle_session_shares_request(
        ctx,
        method_name=method_name,
        path=path,
        body=body,
        query=query,
    )
    if share_result is not None:
        return share_result
    if (
        method_name == "POST"
        and (apply_route := _CONTINUATION_APPLY_RE.fullmatch(path)) is not None
    ):
        return handle_apply_continuation(
            ctx,
            target_session_id=unquote(apply_route.group(1)),
            packet_id=unquote(apply_route.group(2)),
        )
    if (
        method_name == "POST"
        and (build_route := _CONTINUATIONS_RE.fullmatch(path)) is not None
    ):
        return handle_build_continuation(
            ctx,
            source_session_id=unquote(build_route.group(1)),
            body=body,
        )
    if (
        method_name == "POST"
        and (events_route := _EVENTS_RE.fullmatch(path)) is not None
    ):
        return handle_append_session_event(
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

    context_trace_result = maybe_handle_context_traces_request(
        ctx,
        method_name=method_name,
        path=path,
        query=query,
    )
    if context_trace_result is not None:
        return context_trace_result

    return None
