from __future__ import annotations

import re
from http import HTTPStatus
from typing import Any
from urllib.parse import unquote

from openminion.api.core.deps import resolve_runtime_manager
from openminion.api.config import close_api_runtime_if_owned, resolve_api_runtime
from openminion.api.queries.sessions import (
    SessionQueryError, cancel_client_turn, close_client_session,
    create_client_session, list_client_event_page, list_client_session_page,
    load_client_session,
)  # fmt: skip

from . import client_approvals as approvals
from .contracts import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    runtime_unavailable_route_result,
)


_SESSION_PATH = re.compile(r"/v1/client/sessions/([^/]+)")
_EVENTS_PATH = re.compile(r"/v1/client/sessions/([^/]+)/events")
_CANCEL_ERRORS = {
    "invalid_request": (HTTPStatus.BAD_REQUEST, "Cancellation requires exactly one non-empty session_id."),
    "trace_session_mismatch": (HTTPStatus.CONFLICT, "Active trace belongs to another session."),
    "stale_trace": (HTTPStatus.CONFLICT, "Trace is durable but no longer active."),
    "trace_not_found": (HTTPStatus.NOT_FOUND, "Trace was not found in the supplied session."),
    "cancellation_event_failed": (HTTPStatus.INTERNAL_SERVER_ERROR, "Cancellation could not be recorded."),
}  # fmt: skip


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    operation, session_id = _operation(method_name, path)
    if operation is None:
        return None
    if ctx.client_auth is None or ctx.client_identity is None:
        return _query_error(
            SessionQueryError("Request is not authorized.", code="forbidden")
        )
    try:
        runtime, own_runtime = resolve_api_runtime(
            config_path=ctx.config_path,
            runtime=ctx.runtime,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return runtime_unavailable_route_result(path=path, exc=exc)
    try:
        payload = _execute(
            operation,
            ctx=ctx,
            runtime=runtime,
            path=path,
            session_id=session_id,
            body=body,
            query=query,
        )
        resolved_session_id = str(payload["session"]["session_id"]) if operation == "create" else session_id or None  # fmt: skip
        return RouteResult(
            status=HTTPStatus.OK,
            payload=payload,
            session_id=resolved_session_id,
        )
    except SessionQueryError as exc:
        return _query_error(exc, session_id=session_id or None)
    except ValueError as exc:
        return _query_error(
            SessionQueryError(str(exc), code="invalid_request"),
            session_id=session_id or None,
        )
    finally:
        close_api_runtime_if_owned(runtime, own_runtime=own_runtime)


def _operation(method: str, path: str) -> tuple[str | None, str]:
    if path == "/v1/client/sessions":
        return {"GET": "list", "POST": "create"}.get(method), ""
    if match := _EVENTS_PATH.fullmatch(path):
        return ("events" if method == "GET" else None), unquote(match.group(1))
    if match := _SESSION_PATH.fullmatch(path):
        return {"GET": "load", "DELETE": "close"}.get(method), unquote(match.group(1))
    return None, ""


def _execute(
    operation: str,
    *,
    ctx: APIRouteContext,
    runtime: Any,
    path: str,
    session_id: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> dict[str, Any]:
    assert ctx.client_auth is not None
    shared: dict[str, Any] = {
        "runtime": runtime,
        "sign_cursor": ctx.client_auth.sign_cursor,
    }
    if operation == "list":
        return list_client_session_page(
            **shared,
            query=query,
            verify_cursor=ctx.client_auth.verify_cursor,
            request_id=ctx.request_id,
            path=path,
        )
    if operation == "create":
        return create_client_session(**shared, body=body, query=query)[0]
    if operation == "load":
        return load_client_session(
            **shared,
            session_id=session_id,
            query=query,
            verify_cursor=ctx.client_auth.verify_cursor,
            request_id=ctx.request_id,
            path=path,
        )
    if operation == "close":
        return close_client_session(
            **shared,
            session_id=session_id,
            body=body,
            query=query,
            cancel_pending=_session_cancel_callback(ctx),
        )
    return list_client_event_page(
        **shared,
        session_id=session_id,
        query=query,
        verify_cursor=ctx.client_auth.verify_cursor,
        request_id=ctx.request_id,
        path=path,
        approval_recovery=approvals.recovery_callback(ctx, session_id),
    )


def _session_cancel_callback(ctx: APIRouteContext) -> Any:
    owners = tuple(
        owner
        for owner in (ctx.client_approvals, ctx.client_media, ctx.client_artifacts)
        if owner is not None
    )
    if not owners:
        return None

    def cancel(session_id: str) -> Any:
        cleanups = [
            owner.cancel_session(session_id, "session_closed") for owner in owners
        ]

        def finish() -> None:
            for cleanup in reversed(cleanups):
                if cleanup is not None:
                    cleanup()

        return finish

    return cancel


def _query_error(
    exc: SessionQueryError,
    *,
    session_id: str | None = None,
) -> RouteResult:
    statuses = {
        "forbidden": HTTPStatus.FORBIDDEN,
        "session_not_found": HTTPStatus.NOT_FOUND,
        "cursor_expired": HTTPStatus.GONE,
        "session_too_large": HTTPStatus.UNPROCESSABLE_ENTITY,
        "message_too_large": HTTPStatus.UNPROCESSABLE_ENTITY,
        "event_too_large": HTTPStatus.UNPROCESSABLE_ENTITY,
    }
    return error_route_result(
        statuses.get(exc.code, HTTPStatus.BAD_REQUEST),
        code=exc.code,
        message=str(exc),
        details={"reload": True} if exc.code == "cursor_expired" else {},
        retryable=False,
        session_id=session_id,
    )


def handle_cancel_request(
    ctx: APIRouteContext,
    *,
    path: str,
    trace_id: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult:
    if query or not isinstance(body, dict) or set(body) != {"session_id"}:
        return _cancel_error("invalid_request", session_id=None, trace_id=trace_id)
    session_value = body.get("session_id")
    session_id = session_value.strip() if isinstance(session_value, str) else ""
    if not session_id or not trace_id.strip():
        return _cancel_error(
            "invalid_request", session_id=session_id or None, trace_id=trace_id
        )
    try:
        manager, runtime, own_runtime = resolve_runtime_manager(
            config_path=ctx.config_path,
            runtime=ctx.runtime,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return runtime_unavailable_route_result(
            path=path,
            exc=exc,
            session_id=session_id,
        )
    try:
        try:
            result = cancel_client_turn(
                manager,
                runtime,
                session_id=session_id,
                trace_id=trace_id,
            )
        except SessionQueryError as exc:
            return _query_error(exc, session_id=session_id)
        approvals.cancel_trace(ctx, session_id, trace_id)
        if code := result.get("error"):
            return _cancel_error(
                str(code),
                session_id=session_id,
                trace_id=trace_id,
                run_id=result.get("run_id"),
                retryable=bool(result.get("retryable")),
            )
        status = (
            HTTPStatus.ACCEPTED if result["state"] == "requested" else HTTPStatus.OK
        )
        return RouteResult(
            status=status,
            payload={"ok": True, "cancellation": result},
            session_id=session_id,
            run_id=result.get("run_id"),
        )
    finally:
        if own_runtime:
            runtime.close()


def _cancel_error(
    code: str,
    *,
    session_id: str | None,
    trace_id: str,
    run_id: Any = None,
    retryable: bool = False,
) -> RouteResult:
    status, message = _CANCEL_ERRORS[code]
    resolved_run_id = str(run_id or "").strip() or None
    return error_route_result(
        status,
        code=code,
        message=message,
        details={
            **({"session_id": session_id} if session_id else {}),
            **({"trace_id": trace_id} if trace_id else {}),
            **({"run_id": resolved_run_id} if resolved_run_id else {}),
        },
        retryable=retryable,
        session_id=session_id,
        run_id=resolved_run_id,
    )


__all__ = ["handle_cancel_request", "handle_request"]
