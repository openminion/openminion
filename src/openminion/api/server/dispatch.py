"""Ordered API route dispatch."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any, Mapping

from openminion.api.responses.serialization import error_response, normalize_request_id
from openminion.api.routes import (
    APIRouteContext,
    RouteResult,
    handle_admin_request,
    handle_agent_request,
    handle_cron_request,
    handle_debug_request,
    handle_health_request,
    handle_memory_request,
    handle_runtime_request,
    handle_sessions_request,
    handle_skill_request,
    handle_tasks_request,
    handle_tools_request,
    handle_turns_request,
)
from openminion.api.runtime import APIRuntime
from openminion.api.server.observability import finalize_api_response


_ROUTE_HANDLERS = (
    handle_agent_request,
    handle_tools_request,
    handle_cron_request,
    handle_debug_request,
    handle_turns_request,
    handle_sessions_request,
    handle_memory_request,
    handle_skill_request,
    handle_tasks_request,
    handle_admin_request,
)


def dispatch_request(
    method: str,
    path: str,
    config_path: str | None,
    body: dict[str, Any] | None = None,
    query: str | None = None,
    runtime: APIRuntime | None = None,
    runtime_bootstrap_error: str | None = None,
    request_headers: Mapping[str, str] | None = None,
    request_id: str | None = None,
) -> tuple[HTTPStatus, dict[str, Any]]:
    from openminion.api import server

    method_name = method.upper().strip() or "GET"
    resolved_request_id = normalize_request_id(request_id)
    started_at = server.perf_counter()
    logger = logging.getLogger("openminion.api")
    logger.info(
        "api request start method=%s path=%s request_id=%s",
        method_name,
        path,
        resolved_request_id,
    )
    result = _select_route(
        APIRouteContext(
            config_path=config_path,
            runtime=runtime,
            runtime_bootstrap_error=runtime_bootstrap_error,
            request_headers=request_headers,
            request_id=resolved_request_id,
        ),
        method_name=method_name,
        path=path,
        body=body,
        query=query,
        runtime_bootstrap_error=runtime_bootstrap_error,
    )
    return result.status, finalize_api_response(
        payload=result.payload,
        status=result.status,
        method=method_name,
        path=path,
        request_id=resolved_request_id,
        started_at=started_at,
        logger=logger,
        session_id=result.session_id,
        run_id=result.run_id,
    )


def _select_route(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
    runtime_bootstrap_error: str | None,
) -> RouteResult:
    result = handle_health_request(
        ctx,
        method_name=method_name,
        path=path,
        body=body,
        query=query,
    )
    if result is None:
        result = handle_runtime_request(
            ctx,
            method_name=method_name,
            path=path,
            body=body,
            query=query,
        )
    if result is None and runtime_bootstrap_error:
        return _error_result(
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="runtime_unavailable",
            message=(
                "API runtime is in degraded mode because startup bootstrap failed. "
                "Use GET /health for details."
            ),
            details={
                "path": path,
                "bootstrap_error": runtime_bootstrap_error,
                "recovery_path": "/health",
                "recommendation": "Check `degraded_recovery` in GET /health and run `openminion doctor --json`.",
            },
            retryable=True,
            retry_after_ms=1000,
        )
    for handler in _ROUTE_HANDLERS if result is None else ():
        result = handler(
            ctx,
            method_name=method_name,
            path=path,
            body=body,
            query=query,
        )
        if result is not None:
            break
    return result or _error_result(
        HTTPStatus.NOT_FOUND,
        code="not_found",
        message=f"Unknown path: {path}",
        details={"path": path},
        retryable=False,
    )


def _error_result(
    status: HTTPStatus,
    *,
    code: str,
    message: str,
    details: dict[str, Any],
    retryable: bool,
    retry_after_ms: int | None = None,
) -> RouteResult:
    resolved_status, payload = error_response(
        status,
        code=code,
        message=message,
        details=details,
        retryable=retryable,
        retry_after_ms=retry_after_ms,
    )
    return RouteResult(status=resolved_status, payload=payload)


__all__ = ["dispatch_request"]
