from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Mapping
from urllib.parse import parse_qs

from openminion.api.responses.serialization import error_response
from openminion.api.runtime import APIRuntime


@dataclass(frozen=True)
class APIRouteContext:
    config_path: str | None
    runtime: APIRuntime | None
    runtime_bootstrap_error: str | None
    request_headers: Mapping[str, str] | None
    request_id: str


@dataclass(frozen=True)
class RouteResult:
    status: HTTPStatus
    payload: dict[str, Any]
    session_id: str | None = None
    run_id: str | None = None


def query_value(query: str | None, name: str) -> str | None:
    values = parse_qs(str(query or ""), keep_blank_values=False).get(name, [])
    if not values:
        return None
    return str(values[0] or "").strip() or None


def error_route_result(
    status: HTTPStatus,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    error: Any = None,
    **kwargs: Any,
) -> RouteResult:
    resolved_status, payload = error_response(status, error=error, **kwargs)
    return RouteResult(
        status=resolved_status,
        payload=payload,
        session_id=session_id,
        run_id=run_id,
    )


def exception_route_result(
    status: HTTPStatus,
    *,
    code: str,
    exc: Exception,
    details: Mapping[str, Any] | None = None,
    retryable: bool,
    retry_after_ms: int | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
) -> RouteResult:
    return error_route_result(
        status,
        code=code,
        message=str(exc),
        details=dict(details or {}),
        retryable=retryable,
        retry_after_ms=retry_after_ms,
        session_id=session_id,
        run_id=run_id,
    )


def runtime_unavailable_route_result(
    *,
    path: str,
    exc: Exception | str,
    session_id: str | None = None,
    run_id: str | None = None,
) -> RouteResult:
    if isinstance(exc, Exception):
        return exception_route_result(
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="runtime_unavailable",
            exc=exc,
            details={"path": path},
            retryable=True,
            retry_after_ms=1000,
            session_id=session_id,
            run_id=run_id,
        )
    return error_route_result(
        HTTPStatus.SERVICE_UNAVAILABLE,
        code="runtime_unavailable",
        message=str(exc),
        details={"path": path},
        retryable=True,
        retry_after_ms=1000,
        session_id=session_id,
        run_id=run_id,
    )


def json_body_required_route_result(
    *,
    path: str,
    session_id: str | None = None,
    run_id: str | None = None,
) -> RouteResult:
    return error_route_result(
        HTTPStatus.BAD_REQUEST,
        code="invalid_request",
        message="JSON request body is required.",
        details={"path": path},
        retryable=False,
        session_id=session_id,
        run_id=run_id,
    )
