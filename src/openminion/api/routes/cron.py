"""Cron route handlers for the developer API."""

from __future__ import annotations

import re
from http import HTTPStatus
from urllib.parse import unquote

from openminion.api.operations.cron import (
    create_cron_job,
    delete_cron_job,
    trigger_cron_job,
)
from openminion.api.queries.cron import list_cron_jobs

from .base import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    json_body_required_route_result,
)


_JOBS_RE = re.compile(r"/v1/cron/jobs")
_JOB_TRIGGER_RE = re.compile(r"/v1/cron/jobs/([^/]+)/trigger")
_JOB_RE = re.compile(r"/v1/cron/jobs/([^/]+)")


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict | None,
    query: str | None,
) -> RouteResult | None:
    if method_name == "GET" and _JOBS_RE.fullmatch(path):
        return _list_jobs(ctx)
    if method_name == "POST" and _JOBS_RE.fullmatch(path):
        return _create_job(ctx, body=body)
    # Trigger must be checked before the bare job match.
    if method_name == "POST" and (m := _JOB_TRIGGER_RE.fullmatch(path)):
        return _trigger_job(ctx, job_id=unquote(m.group(1)))
    if method_name == "DELETE" and (m := _JOB_RE.fullmatch(path)):
        return _delete_job(ctx, job_id=unquote(m.group(1)))
    return None


def _runtime_unavailable(path: str) -> RouteResult:
    return error_route_result(
        HTTPStatus.SERVICE_UNAVAILABLE,
        code="runtime_unavailable",
        message="Runtime not available.",
        details={"path": path},
        retryable=True,
    )


def _list_jobs(ctx: APIRouteContext) -> RouteResult:
    if ctx.runtime is None:
        return _runtime_unavailable("/v1/cron/jobs")
    try:
        jobs = list_cron_jobs(runtime=ctx.runtime)
        return RouteResult(status=HTTPStatus.OK, payload={"ok": True, "jobs": jobs})
    except Exception as exc:
        return error_route_result(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="cron_error",
            message=str(exc),
            details={},
            retryable=False,
        )


def _create_job(ctx: APIRouteContext, *, body: dict | None) -> RouteResult:
    if ctx.runtime is None:
        return _runtime_unavailable("/v1/cron/jobs")
    if body is None:
        return json_body_required_route_result(path="/v1/cron/jobs")
    try:
        job_id = create_cron_job(runtime=ctx.runtime, body=body)
        return RouteResult(
            status=HTTPStatus.CREATED, payload={"ok": True, "job_id": job_id}
        )
    except (ValueError, KeyError, TypeError) as exc:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message=str(exc),
            details={},
            retryable=False,
        )
    except Exception as exc:
        return error_route_result(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="cron_error",
            message=str(exc),
            details={},
            retryable=False,
        )


def _trigger_job(ctx: APIRouteContext, *, job_id: str) -> RouteResult:
    if ctx.runtime is None:
        return _runtime_unavailable(f"/v1/cron/jobs/{job_id}/trigger")
    try:
        run_id = trigger_cron_job(runtime=ctx.runtime, job_id=job_id)
        return RouteResult(status=HTTPStatus.OK, payload={"ok": True, "run_id": run_id})
    except ValueError as exc:
        return error_route_result(
            HTTPStatus.NOT_FOUND,
            code="cron_job_not_found",
            message=str(exc),
            details={"job_id": job_id},
            retryable=False,
        )
    except Exception as exc:
        return error_route_result(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="cron_error",
            message=str(exc),
            details={"job_id": job_id},
            retryable=False,
        )


def _delete_job(ctx: APIRouteContext, *, job_id: str) -> RouteResult:
    if ctx.runtime is None:
        return _runtime_unavailable(f"/v1/cron/jobs/{job_id}")
    try:
        delete_cron_job(runtime=ctx.runtime, job_id=job_id)
        return RouteResult(status=HTTPStatus.OK, payload={"ok": True})
    except ValueError as exc:
        return error_route_result(
            HTTPStatus.NOT_FOUND,
            code="cron_job_not_found",
            message=str(exc),
            details={"job_id": job_id},
            retryable=False,
        )
    except Exception as exc:
        return error_route_result(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="cron_error",
            message=str(exc),
            details={"job_id": job_id},
            retryable=False,
        )
