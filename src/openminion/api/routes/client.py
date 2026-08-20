"""Authenticated local-client capability and lease routes."""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import unquote

from .contracts import APIRouteContext, RouteResult, error_route_result
from .client_approvals import handle_request as handle_client_approval_request
from .client_sessions import (
    handle_cancel_request,
    handle_request as handle_client_sessions_request,
)

if TYPE_CHECKING:
    from openminion.api.server.client_auth import ClientAuthError


_TURN_CANCEL_PATH = re.compile(r"/v1/turn/([^/]+)/cancel")


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    approval_result = handle_client_approval_request(
        ctx,
        method_name=method_name,
        path=path,
        body=body,
        query=query,
    )
    if approval_result is not None:
        return approval_result
    if (
        ctx.client_identity is not None
        and method_name == "POST"
        and (cancel_match := _TURN_CANCEL_PATH.fullmatch(path)) is not None
    ):
        return handle_cancel_request(
            ctx,
            path=path,
            trace_id=unquote(cancel_match.group(1)),
            body=body,
            query=query,
        )
    session_result = handle_client_sessions_request(
        ctx,
        method_name=method_name,
        path=path,
        body=body,
        query=query,
    )
    if session_result is not None:
        return session_result
    if path == "/v1/client/leases" and method_name == "POST":
        return _mint(ctx, body=body, query=query)
    if path == "/v1/client/capabilities" and method_name == "GET":
        return _capabilities(ctx, body=body, query=query)
    if path == "/v1/client/leases/renew" and method_name == "POST":
        return _renew(ctx, body=body, query=query)
    if path == "/v1/client/leases/current" and method_name == "DELETE":
        return _revoke(ctx, body=body, query=query)
    return None


def _mint(
    ctx: APIRouteContext,
    *,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult:
    from openminion.api.server.client_auth import ClientAuthError

    if query:
        return _invalid("Query fields are not supported.")
    if ctx.client_auth is None:
        return _unavailable()
    payload = body or {}
    if set(payload) != {"schema_version", "client", "requested_ttl_seconds"}:
        return _invalid("Lease request fields do not match schema version 1.")
    client = payload.get("client")
    if not isinstance(client, dict) or set(client) != {
        "kind",
        "version",
        "protocol_min",
        "protocol_max",
    }:
        return _invalid("client fields do not match schema version 1.")
    if payload.get("schema_version") != 1 or client.get("kind") != "desktop":
        return _invalid("Only schema version 1 desktop clients are supported.")
    version = client.get("version")
    if not isinstance(version, str) or not version.strip() or len(version) > 128:
        return _invalid("client.version must be a bounded non-empty string.")
    protocol_min = client.get("protocol_min")
    protocol_max = client.get("protocol_max")
    ttl_seconds = payload.get("requested_ttl_seconds")
    if not all(
        type(value) is int for value in (protocol_min, protocol_max, ttl_seconds)
    ):
        return _invalid("Protocol and TTL fields must be integers.")
    try:
        lease = ctx.client_auth.mint(
            protocol_min=cast(int, protocol_min),
            protocol_max=cast(int, protocol_max),
            ttl_seconds=cast(int, ttl_seconds),
        )
    except ClientAuthError as exc:
        return _auth_error(exc)
    return RouteResult(status=HTTPStatus.OK, payload={"ok": True, "lease": lease})


def _capabilities(
    ctx: APIRouteContext,
    *,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult:
    if body or query:
        return _invalid("Capabilities does not accept a body or query.")
    if ctx.client_auth is None or ctx.client_identity is None:
        return _forbidden()
    return RouteResult(
        status=HTTPStatus.OK,
        payload={"ok": True, **ctx.client_auth.capabilities(ctx.client_identity)},
    )


def _renew(
    ctx: APIRouteContext,
    *,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult:
    from openminion.api.server.client_auth import ClientAuthError

    if body or query:
        return _invalid("Lease renewal accepts only an empty JSON body.")
    if ctx.client_auth is None or ctx.client_identity is None:
        return _forbidden()
    try:
        lease = ctx.client_auth.renew(ctx.client_identity)
    except ClientAuthError as exc:
        return _auth_error(exc)
    return RouteResult(status=HTTPStatus.OK, payload={"ok": True, "lease": lease})


def _revoke(
    ctx: APIRouteContext,
    *,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult:
    from openminion.api.server.client_auth import ClientAuthError

    if body or query:
        return _invalid("Lease revocation accepts only an empty JSON body.")
    if ctx.client_auth is None or ctx.client_identity is None:
        return _forbidden()
    try:
        ctx.client_auth.revoke(ctx.client_identity)
    except ClientAuthError as exc:
        return _auth_error(exc)
    if ctx.client_approvals is not None:
        ctx.client_approvals.cancel_client(ctx.client_identity.client_id, "revoked")
    return RouteResult(status=HTTPStatus.OK, payload={"ok": True, "revoked": True})


def _invalid(message: str) -> RouteResult:
    return error_route_result(
        HTTPStatus.BAD_REQUEST,
        code="invalid_request",
        message=message,
        details={},
        retryable=False,
    )


def _unavailable() -> RouteResult:
    return error_route_result(
        HTTPStatus.SERVICE_UNAVAILABLE,
        code="desktop_auth_unavailable",
        message="Desktop authentication is unavailable.",
        details={},
        retryable=False,
    )


def _forbidden() -> RouteResult:
    return error_route_result(
        HTTPStatus.FORBIDDEN,
        code="forbidden",
        message="Request is not authorized.",
        details={},
        retryable=False,
    )


def _auth_error(exc: ClientAuthError) -> RouteResult:
    status = (
        HTTPStatus.BAD_REQUEST
        if exc.code in {"invalid_request", "unsupported_protocol"}
        else HTTPStatus.FORBIDDEN
    )
    return error_route_result(
        status,
        code=exc.code,
        message=str(exc),
        details={},
        retryable=False,
    )
