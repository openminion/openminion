"""Route support for session context-trace inspection."""

from __future__ import annotations

import re
from http import HTTPStatus
from urllib.parse import parse_qs, unquote

from openminion.api.queries.sessions import (
    SessionQueryError,
    list_session_context_traces,
)
from openminion.api.routes.contracts import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    exception_route_result,
)

_CONTEXT_TRACES_RE = re.compile(r"/sessions/([^/]+)/context-traces")


def maybe_handle_context_traces_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    query: str | None,
) -> RouteResult | None:
    if (
        method_name != "GET"
        or (trace_route := _CONTEXT_TRACES_RE.fullmatch(path)) is None
    ):
        return None
    return _handle_list_context_traces(
        ctx,
        session_id=unquote(trace_route.group(1)),
        query=query,
    )


def _handle_list_context_traces(
    ctx: APIRouteContext,
    *,
    session_id: str,
    query: str | None,
) -> RouteResult:
    query_args = parse_qs(query or "", keep_blank_values=False)
    limit, invalid = _parse_limit(
        query_args.get("limit", [None])[0],
        session_id=session_id,
    )
    if invalid is not None:
        return invalid
    turn_id = str(query_args.get("turn_id", [""])[0] or "").strip() or None
    try:
        payload = {
            "ok": True,
            **list_session_context_traces(
                config_path=ctx.config_path,
                session_id=session_id,
                turn_id=turn_id,
                limit=limit,
                runtime=ctx.runtime,
            ),
        }
    except SessionQueryError as exc:
        return exception_route_result(
            HTTPStatus.NOT_FOUND,
            code=exc.code,
            exc=exc,
            details={"session_id": session_id, "turn_id": turn_id},
            retryable=False,
            session_id=session_id,
        )
    return RouteResult(status=HTTPStatus.OK, payload=payload, session_id=session_id)


def _parse_limit(
    raw_limit: str | None,
    *,
    session_id: str,
) -> tuple[int, RouteResult | None]:
    if raw_limit is None:
        return 50, None
    try:
        return int(raw_limit), None
    except ValueError:
        return 0, error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`limit` must be an integer.",
            details={"query": {"limit": raw_limit}},
            retryable=False,
            session_id=session_id,
        )


__all__ = ["maybe_handle_context_traces_request"]
