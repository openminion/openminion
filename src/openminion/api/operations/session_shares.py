"""Session share API operations."""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, unquote

from openminion.api.routes.contracts import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    exception_route_result,
)
from openminion.modules.session.sharing import (
    SessionShareDeniedError,
    SessionShareError,
    SessionShareExpiredError,
    SessionShareNotFoundError,
    SessionShareRateLimitedError,
    SessionShareRevokedError,
    SessionShareService,
    SessionShareTokenTransportError,
    extract_bearer_token,
    reject_forbidden_token_transport,
)

_SESSION_SHARES_RE = re.compile(r"(?:/v1)?/sessions/([^/]+)/shares")
_SESSION_SHARE_RE = re.compile(r"(?:/v1)?/session-shares/([^/]+)")
_SHARE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def maybe_handle_session_shares_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    if (share_route := _SESSION_SHARES_RE.fullmatch(path)) is not None:
        return _handle_session_share_collection(
            ctx,
            method_name=method_name,
            session_id=unquote(share_route.group(1)),
            body=body,
        )
    if (share_item_route := _SESSION_SHARE_RE.fullmatch(path)) is not None:
        return _handle_session_share_item(
            ctx,
            method_name=method_name,
            share_id=unquote(share_item_route.group(1)),
            query=query,
        )
    return None


def _handle_session_share_collection(
    ctx: APIRouteContext,
    *,
    method_name: str,
    session_id: str,
    body: dict[str, Any] | None,
) -> RouteResult | None:
    if method_name == "POST":
        return _handle_create_session_share(ctx, session_id=session_id, body=body)
    if method_name == "GET":
        return _handle_list_session_shares(ctx, session_id=session_id)
    return None


def _handle_session_share_item(
    ctx: APIRouteContext,
    *,
    method_name: str,
    share_id: str,
    query: str | None,
) -> RouteResult | None:
    if method_name == "GET":
        return _handle_access_session_share(ctx, share_id=share_id, query=query)
    if method_name == "DELETE":
        return _handle_revoke_session_share(ctx, share_id=share_id)
    return None


def _share_service_or_error(
    ctx: APIRouteContext,
    *,
    path: str,
    session_id: str | None = None,
) -> tuple[SessionShareService | None, RouteResult | None]:
    session_store = getattr(ctx.runtime, "sessions", None) if ctx.runtime else None
    if session_store is None:
        return None, error_route_result(
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="runtime_unavailable",
            message="Session sharing requires an initialized runtime.",
            details={"path": path},
            retryable=True,
            retry_after_ms=1000,
            session_id=session_id,
        )
    return SessionShareService(session_store), None


def _share_error_result(
    exc: Exception,
    *,
    session_id: str | None = None,
    share_id: str | None = None,
) -> RouteResult:
    code = str(getattr(exc, "code", "SESSION_SHARE_ERROR"))
    details = dict(getattr(exc, "details", {}) or {})
    if share_id is not None:
        details["share_id"] = share_id
    return exception_route_result(
        _status_for_share_error(code),
        code=code,
        exc=exc,
        details=details,
        retryable=code == SessionShareRateLimitedError.code,
        retry_after_ms=1000 if code == SessionShareRateLimitedError.code else None,
        session_id=session_id,
    )


def _status_for_share_error(code: str) -> HTTPStatus:
    return {
        SessionShareTokenTransportError.code: HTTPStatus.BAD_REQUEST,
        SessionShareDeniedError.code: HTTPStatus.UNAUTHORIZED,
        SessionShareNotFoundError.code: HTTPStatus.NOT_FOUND,
        SessionShareExpiredError.code: HTTPStatus.GONE,
        SessionShareRevokedError.code: HTTPStatus.GONE,
        SessionShareRateLimitedError.code: HTTPStatus.TOO_MANY_REQUESTS,
    }.get(code, HTTPStatus.BAD_REQUEST)


def _handle_create_session_share(
    ctx: APIRouteContext,
    *,
    session_id: str,
    body: dict[str, Any] | None,
) -> RouteResult:
    service, error = _share_service_or_error(ctx, path="/sessions/{id}/shares", session_id=session_id)
    if error is not None:
        return error
    assert service is not None
    try:
        created = service.create_share(
            session_id=session_id,
            created_by=str((body or {}).get("created_by") or "operator"),
            ttl_seconds=int((body or {}).get("ttl_seconds") or 3600),
            projection_policy=(body or {}).get("projection_policy"),
        )
    except SessionShareError as exc:
        return _share_error_result(exc, session_id=session_id)
    return RouteResult(
        status=HTTPStatus.CREATED,
        payload={"ok": True, "share": created.response_payload(), "meta": _meta()},
        session_id=session_id,
    )


def _handle_list_session_shares(ctx: APIRouteContext, *, session_id: str) -> RouteResult:
    service, error = _share_service_or_error(ctx, path="/sessions/{id}/shares", session_id=session_id)
    if error is not None:
        return error
    assert service is not None
    return RouteResult(
        status=HTTPStatus.OK,
        payload={"ok": True, "shares": service.list_shares(session_id), "meta": _meta()},
        session_id=session_id,
    )


def _handle_access_session_share(
    ctx: APIRouteContext,
    *,
    share_id: str,
    query: str | None,
) -> RouteResult:
    service, error = _share_service_or_error(ctx, path="/session-shares/{id}")
    if error is not None:
        return error
    assert service is not None
    try:
        reject_forbidden_token_transport(
            query_args=parse_qs(query or "", keep_blank_values=False),
            cookies=(ctx.request_headers or {}).get("Cookie") if ctx.request_headers else None,
        )
        projection = service.access_share(
            share_id=share_id,
            token=extract_bearer_token(ctx.request_headers),
        )
    except SessionShareError as exc:
        return _share_error_result(exc, share_id=share_id)
    return RouteResult(
        status=HTTPStatus.OK,
        payload={"ok": True, "projection": projection, "meta": _meta()},
        session_id=str(projection.get("share", {}).get("session_id") or "") or None,
    )


def _handle_revoke_session_share(ctx: APIRouteContext, *, share_id: str) -> RouteResult:
    service, error = _share_service_or_error(ctx, path="/session-shares/{id}")
    if error is not None:
        return error
    assert service is not None
    try:
        record = service.revoke_share(share_id)
    except SessionShareError as exc:
        return _share_error_result(exc, share_id=share_id)
    return RouteResult(
        status=HTTPStatus.OK,
        payload={"ok": True, "share": record.public_dict(), "meta": _meta()},
        session_id=record.session_id,
    )


def _meta() -> dict[str, Any]:
    return {"response_headers": dict(_SHARE_HEADERS)}
