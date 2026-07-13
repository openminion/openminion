"""API response metadata, metrics, and request logging."""

from __future__ import annotations

import logging
import re
from http import HTTPStatus
from typing import Any, Optional

from openminion.api import metrics_registry
from openminion.api.responses.serialization import (
    attach_response_meta,
    response_error_code,
)


_SLOW_REQUEST_WARN_MS = 1000
_EXACT_ROUTES = {
    ("GET", "/health"): "GET /health",
    ("GET", "/v1/health"): "GET /v1/health",
    ("GET", "/metrics"): "GET /metrics",
    ("GET", "/v1/agents"): "GET /v1/agents",
    ("GET", "/v1/tools"): "GET /v1/tools",
    ("GET", "/owner/status"): "GET /owner/status",
    ("POST", "/v1/turn"): "POST /v1/turn",
    ("POST", "/v1/turn/stream"): "POST /v1/turn/stream",
    ("GET", "/v1/cron/jobs"): "GET /v1/cron/jobs",
    ("POST", "/v1/cron/jobs"): "POST /v1/cron/jobs",
    ("POST", "/v1/admin/kill"): "POST /v1/admin/kill",
    ("POST", "/turns"): "POST /turns",
}
_PATTERN_ROUTES = (
    ("GET", re.compile(r"/v1/tools/([^/]+)/schema"), "GET /v1/tools/{tool}/schema"),
    ("POST", re.compile(r"/v1/tools/([^/]+)/run"), "POST /v1/tools/{tool}/run"),
    ("POST", re.compile(r"/v1/turn/([^/]+)/cancel"), "POST /v1/turn/{trace_id}/cancel"),
    ("POST", re.compile(r"/v1/agents/([^/]+)/evict"), "POST /v1/agents/{id}/evict"),
    ("GET", re.compile(r"/v1/agents/([^/]+)/inspect"), "GET /v1/agents/{id}/inspect"),
    (
        "POST",
        re.compile(r"/v1/cron/jobs/([^/]+)/trigger"),
        "POST /v1/cron/jobs/{id}/trigger",
    ),
    ("DELETE", re.compile(r"/v1/cron/jobs/([^/]+)"), "DELETE /v1/cron/jobs/{id}"),
    ("GET", re.compile(r"/sessions/([^/]+)/runs"), "GET /sessions/{id}/runs"),
    (
        "GET",
        re.compile(r"/sessions/([^/]+)/runs/([^/]+)/events"),
        "GET /sessions/{id}/runs/{run_id}/events",
    ),
    ("GET", re.compile(r"/sessions/([^/]+)/messages"), "GET /sessions/{id}/messages"),
)


def finalize_api_response(
    *,
    payload: dict[str, Any],
    status: HTTPStatus,
    method: str,
    path: str,
    request_id: str,
    started_at: float,
    logger: logging.Logger,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    response = attach_response_meta(
        payload,
        request_id=request_id,
        method=method,
        path=path,
        session_id=session_id,
        run_id=run_id,
    )
    duration_ms = observe_request_metrics(
        method=method, path=path, status=status, payload=payload, started_at=started_at
    )
    log_request_done(
        logger=logger,
        method=method,
        path=path,
        status=status,
        request_id=request_id,
        duration_ms=duration_ms,
        session_id=session_id,
        run_id=run_id,
    )
    return response


def get_api_metrics_snapshot(*, reset: bool = False) -> dict[str, Any]:
    return metrics_registry.snapshot(reset=reset)


def get_api_metrics_consistency_stamp() -> dict[str, Any]:
    return metrics_registry.consistency_stamp()


def reset_api_metrics() -> None:
    metrics_registry.reset()


def observe_request_metrics(
    *,
    method: str,
    path: str,
    status: HTTPStatus,
    payload: Optional[dict[str, Any]],
    started_at: float,
) -> int:
    from openminion.api import server

    duration_ms = max(0, int((server.perf_counter() - started_at) * 1000))
    route = route_metric_key(method=method, path=path)
    if route != "GET /metrics":
        metrics_registry.observe(
            route=route,
            status_code=int(status),
            duration_ms=duration_ms,
            error_code=response_error_code(payload),
        )
    return duration_ms


def log_request_done(
    *,
    logger: logging.Logger,
    method: str,
    path: str,
    status: HTTPStatus,
    request_id: str,
    duration_ms: int,
    session_id: Optional[str],
    run_id: Optional[str],
) -> None:
    route = route_metric_key(method=method, path=path)
    values = (
        method,
        path,
        route,
        int(status),
        f"{int(status) // 100}xx",
        request_id,
        session_id or "",
        run_id or "",
        duration_ms,
    )
    logger.info(
        "api request done method=%s path=%s route=%s status=%s status_class=%s request_id=%s session_id=%s run_id=%s duration_ms=%s",
        *values,
    )
    if duration_ms >= _SLOW_REQUEST_WARN_MS:
        logger.warning(
            "api slow request method=%s path=%s route=%s status=%s status_class=%s request_id=%s session_id=%s run_id=%s duration_ms=%s threshold_ms=%s",
            *values,
            _SLOW_REQUEST_WARN_MS,
        )


def route_metric_key(*, method: str, path: str) -> str:
    method_name = method.upper().strip() or "GET"
    exact = _EXACT_ROUTES.get((method_name, path))
    if exact:
        return exact
    for route_method, pattern, label in _PATTERN_ROUTES:
        if method_name == route_method and pattern.fullmatch(path):
            return label
    return f"{method_name} /<unknown>"


__all__ = [
    "finalize_api_response",
    "get_api_metrics_consistency_stamp",
    "get_api_metrics_snapshot",
    "log_request_done",
    "observe_request_metrics",
    "reset_api_metrics",
]
