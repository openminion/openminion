"""Memory provenance API routes."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs

from openminion.modules.memory import default_provenance_recorder

from .base import APIRouteContext, RouteResult, error_route_result


_PROVENANCE_PATH = "/memory/provenance"
_PROVENANCE_BY_MEMORY_PATH = "/memory/provenance/by-memory"


def _single_query_value(query: str | None, name: str) -> str | None:
    if not query:
        return None
    parsed = parse_qs(query, keep_blank_values=False)
    values = parsed.get(name)
    return values[0] if values else None


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    """Handle memory-provenance GET routes or return ``None`` for fallthrough."""

    if method_name != "GET":
        return None
    if path == _PROVENANCE_PATH:
        return _handle_get_turn_trace(path=path, query=query)
    if path == _PROVENANCE_BY_MEMORY_PATH:
        return _handle_get_by_memory(path=path, query=query)
    return None


def _handle_get_turn_trace(
    *,
    path: str,
    query: str | None,
) -> RouteResult:
    session_id = _single_query_value(query, "session_id")
    turn_id = _single_query_value(query, "turn_id")
    if not session_id:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`session_id` query parameter is required.",
            details={"path": path},
            retryable=False,
        )
    if not turn_id:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`turn_id` query parameter is required.",
            details={"path": path},
            retryable=False,
            session_id=session_id,
        )

    trace = default_provenance_recorder().get_turn_trace(
        session_id=session_id,
        turn_id=turn_id,
    )
    if trace is None:
        return error_route_result(
            HTTPStatus.NOT_FOUND,
            code="not_found",
            message=f"no provenance trace recorded for session={session_id} turn={turn_id}",
            details={"path": path},
            retryable=False,
            session_id=session_id,
        )

    return RouteResult(
        status=HTTPStatus.OK,
        payload=trace.to_dict(),
        session_id=session_id,
    )


def _handle_get_by_memory(
    *,
    path: str,
    query: str | None,
) -> RouteResult:
    memory_id = _single_query_value(query, "memory_id")
    if not memory_id:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message="`memory_id` query parameter is required.",
            details={"path": path},
            retryable=False,
        )

    traces = default_provenance_recorder().find_traces_citing_memory(memory_id)
    return RouteResult(
        status=HTTPStatus.OK,
        payload={
            "memory_id": memory_id,
            "trace_count": len(traces),
            "traces": [t.to_dict() for t in traces],
        },
    )
