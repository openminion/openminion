"""Health, metrics, and owner-status route handlers."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs

from openminion.api.core.deps import (
    authorize_metrics_request,
    build_degraded_recovery_hint,
    v1_daemon_health,
)
from openminion.api.constants import API_METRICS_TOKEN_HEADER
from openminion.api.core.validation import (
    parse_bool_query_value,
    parse_positive_int_query_value,
)
from openminion.api.queries.owner import OwnerStatusQueryError, get_owner_status
from openminion.api.responses.serialization import error_response
from openminion.api import metrics_registry

from .base import APIRouteContext, RouteResult


_HEALTH_PATHS = {"/health", "/v1/health"}


def _parse_probe_session_id(query: str | None) -> str | None:
    query_args = parse_qs(query or "", keep_blank_values=False)
    probe_session_raw = query_args.get("session_id", [None])[0]
    probe_session_id = (
        str(probe_session_raw).strip() if isinstance(probe_session_raw, str) else None
    )
    return probe_session_id or None


def _handle_health_request(
    ctx: APIRouteContext,
    *,
    path: str,
    query: str | None,
) -> RouteResult:
    from openminion.services.health.service import collect_health_snapshot

    probe_session_id = _parse_probe_session_id(query)
    payload = collect_health_snapshot(
        config_path=ctx.config_path,
        runtime=ctx.runtime,
        metrics_consistency=metrics_registry.consistency_stamp(),
        probe_session_id=probe_session_id,
    )
    payload = dict(payload)
    if probe_session_id:
        payload["probe_session_id"] = probe_session_id
    if path == "/v1/health":
        payload["daemon"] = v1_daemon_health(
            ctx.runtime,
            config_path=ctx.config_path,
        )
    if ctx.runtime_bootstrap_error:
        payload["degraded"] = True
        payload["degraded_reason"] = ctx.runtime_bootstrap_error
        payload["degraded_recovery"] = build_degraded_recovery_hint(
            config_path=ctx.config_path,
            health_payload=payload,
            bootstrap_error=ctx.runtime_bootstrap_error,
        )
        status = HTTPStatus.SERVICE_UNAVAILABLE
    else:
        status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.SERVICE_UNAVAILABLE
    return RouteResult(status=status, payload=payload)


def _handle_metrics_request(
    ctx: APIRouteContext,
    *,
    path: str,
    query: str | None,
) -> RouteResult:
    denial_message = authorize_metrics_request(ctx.request_headers)
    if denial_message is not None:
        status, payload = error_response(
            HTTPStatus.FORBIDDEN,
            code="forbidden",
            message=denial_message,
            details={"path": path, "required_header": API_METRICS_TOKEN_HEADER},
            retryable=False,
        )
        return RouteResult(status=status, payload=payload)

    query_args = parse_qs(query or "", keep_blank_values=False)
    reset_raw = query_args.get("reset", [None])[0]
    try:
        should_reset = parse_bool_query_value(reset_raw)
    except ValueError as exc:
        status, payload = error_response(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message=str(exc),
            details={"query": {"reset": reset_raw}},
            retryable=False,
        )
    else:
        metrics = metrics_registry.snapshot(reset=should_reset)
        status = HTTPStatus.OK
        payload = {"ok": True, "metrics": metrics, "reset": should_reset}
    return RouteResult(status=status, payload=payload)


def _handle_owner_status_request(
    ctx: APIRouteContext,
    *,
    path: str,
    query: str | None,
) -> RouteResult | None:
    if ctx.runtime_bootstrap_error:
        return None
    query_args = parse_qs(query or "", keep_blank_values=False)
    session_limit_raw = query_args.get("session_limit", [None])[0]
    run_limit_raw = query_args.get("run_limit", [None])[0]
    hours_raw = query_args.get("hours", [None])[0]
    try:
        session_limit = parse_positive_int_query_value(
            raw_value=session_limit_raw,
            default_value=20,
            field_name="session_limit",
        )
        run_limit = parse_positive_int_query_value(
            raw_value=run_limit_raw,
            default_value=20,
            field_name="run_limit",
        )
        hours = parse_positive_int_query_value(
            raw_value=hours_raw,
            default_value=24,
            field_name="hours",
        )
        owner_payload = get_owner_status(
            config_path=ctx.config_path,
            runtime=ctx.runtime,
            session_limit=session_limit,
            run_limit_per_session=run_limit,
            window_hours=hours,
        )
        status = HTTPStatus.OK
        payload = {"ok": True, **owner_payload}
    except ValueError as exc:
        status, payload = error_response(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message=str(exc),
            details={
                "query": {
                    "session_limit": session_limit_raw,
                    "run_limit": run_limit_raw,
                    "hours": hours_raw,
                }
            },
            retryable=False,
        )
    except OwnerStatusQueryError as exc:
        status, payload = error_response(
            HTTPStatus.BAD_REQUEST,
            code=exc.code,
            message=str(exc),
            details={"path": path},
            retryable=False,
        )
    return RouteResult(status=status, payload=payload)


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict | None,
    query: str | None,
) -> RouteResult | None:
    if method_name == "GET" and path in _HEALTH_PATHS:
        return _handle_health_request(ctx, path=path, query=query)

    if method_name == "GET" and path == "/metrics":
        return _handle_metrics_request(ctx, path=path, query=query)

    if method_name == "GET" and path == "/owner/status":
        return _handle_owner_status_request(ctx, path=path, query=query)

    return None
