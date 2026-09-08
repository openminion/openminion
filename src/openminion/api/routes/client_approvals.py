"""Fixed authenticated desktop approval-decision route."""

from __future__ import annotations

from http import HTTPStatus
import re
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import unquote

from .contracts import APIRouteContext, RouteResult, error_route_result

if TYPE_CHECKING:
    from openminion.api.server.client_approvals import ClientApprovalError


_APPROVAL_PATH = re.compile(
    r"/v1/client/sessions/([^/]+)/turns/([^/]+)/approvals/([^/]+)"
)
_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    match = _APPROVAL_PATH.fullmatch(path)
    if match is None or method_name != "POST":
        return None
    if ctx.client_identity is None or ctx.client_approvals is None:
        return _error(HTTPStatus.FORBIDDEN, "forbidden", "Request is not authorized.")
    if query or not isinstance(body, dict) or set(body) != {"decision"}:
        return _invalid_approval()
    decision = body.get("decision")
    if decision not in {"allow_once", "deny"}:
        return _invalid_approval()
    session_id, trace_id, approval_id = (unquote(value) for value in match.groups())
    if any(
        _OPAQUE_ID.fullmatch(value) is None
        for value in (session_id, trace_id, approval_id)
    ):
        return _invalid_approval()
    from openminion.api.server.client_approvals import ClientApprovalError

    try:
        approval = ctx.client_approvals.decide(
            ctx.client_identity,
            session_id=session_id,
            trace_id=trace_id,
            approval_id=approval_id,
            decision=decision,
        )
    except ClientApprovalError as exc:
        return _approval_error(exc)
    return RouteResult(
        status=HTTPStatus.OK,
        payload={"ok": True, "approval": approval},
        session_id=session_id,
        run_id=trace_id,
    )


def _invalid_approval() -> RouteResult:
    return _error(
        HTTPStatus.BAD_REQUEST,
        "invalid_request",
        "Approval decision fields do not match schema version 1.",
    )


def _approval_error(exc: ClientApprovalError) -> RouteResult:
    statuses = {
        "approval_not_found": HTTPStatus.NOT_FOUND,
        "approval_already_resolved": HTTPStatus.CONFLICT,
        "approval_expired": HTTPStatus.GONE,
        "approval_cancelled": HTTPStatus.CONFLICT,
        "approval_event_failed": HTTPStatus.INTERNAL_SERVER_ERROR,
    }
    return _error(statuses[exc.code], exc.code, str(exc))


def _error(status: HTTPStatus, code: str, message: str) -> RouteResult:
    return error_route_result(
        status,
        code=code,
        message=message,
        details={},
        retryable=False,
    )


def session_cancel_callback(ctx: APIRouteContext) -> Callable[[str], None] | None:
    approvals = ctx.client_approvals
    if approvals is None:
        return None
    return lambda session_id: approvals.cancel_session(session_id, "session_closed")


def recovery_callback(
    ctx: APIRouteContext,
    session_id: str,
) -> Callable[[str, str, str], str] | None:
    approvals = ctx.client_approvals
    if approvals is None or ctx.client_identity is None:
        return None
    client_id = ctx.client_identity.client_id
    return lambda trace_id, approval_id, expires_at: approvals.recovery_outcome(
        client_id, session_id, trace_id, approval_id, expires_at
    )


def cancel_trace(ctx: APIRouteContext, session_id: str, trace_id: str) -> None:
    if ctx.client_approvals is not None and ctx.client_identity is not None:
        ctx.client_approvals.cancel_trace(
            ctx.client_identity.client_id, session_id, trace_id, "cancelled"
        )


__all__ = [
    "cancel_trace",
    "handle_request",
    "recovery_callback",
    "session_cancel_callback",
]
