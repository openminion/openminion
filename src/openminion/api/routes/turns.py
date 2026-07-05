"""Turn route handlers for submit, stream, and cancel."""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import Any, cast
from urllib.parse import unquote

from openminion.api.core.turn_execution import (
    close_submission,
    collect_sync_turn_payload,
    open_turn_submission,
)
from openminion.api.core.deps import resolve_runtime_manager
from openminion.api.queries.sessions import append_session_event
from openminion.api.turns import TurnRequestError, TurnTimeoutError, run_turn
from .turn_inputs import handle_request as handle_turn_inputs_request

from .contracts import (
    APIRouteContext,
    RouteResult,
    exception_route_result,
    error_route_result,
    json_body_required_route_result,
    runtime_unavailable_route_result,
)


_CANCEL_RE = re.compile(r"/v1/turn/([^/]+)/cancel")


def _handle_cancel_turn(
    ctx: APIRouteContext,
    *,
    path: str,
    trace_id: str,
    body: dict[str, Any] | None,
) -> RouteResult:
    try:
        manager, active_runtime, own_runtime = resolve_runtime_manager(
            config_path=ctx.config_path,
            runtime=ctx.runtime,
        )
    except Exception as exc:  # noqa: BLE001
        return runtime_unavailable_route_result(path=path, exc=exc)
    try:
        cancelled = bool(cast(Any, manager).cancel_turn(trace_id))
        if not cancelled:
            return error_route_result(
                HTTPStatus.NOT_FOUND,
                code="trace_not_found",
                message=f"Trace not found: {trace_id}",
                details={"trace_id": trace_id},
                retryable=False,
            )
        if cancelled and isinstance(body, dict):
            session_id = str(body.get("session_id", "")).strip()
            if session_id:
                event_payload = {
                    "run_id": str(body.get("run_id", "")).strip() or trace_id,
                    "trace_id": trace_id,
                    "conversation_id": str(body.get("conversation_id", "")).strip(),
                    "thread_id": str(body.get("thread_id", "")).strip(),
                    "attach_id": str(body.get("attach_id", "")).strip(),
                }
                try:
                    append_session_event(
                        config_path=ctx.config_path,
                        session_id=session_id,
                        event_type="run.cancel_requested",
                        payload={k: v for k, v in event_payload.items() if v},
                        runtime=active_runtime,
                    )
                except Exception:
                    pass
    finally:
        if own_runtime:
            active_runtime.close()
    return RouteResult(
        status=HTTPStatus.OK,
        payload={"ok": True, "trace_id": trace_id, "cancelled": True},
    )


def _handle_legacy_turn_request(
    ctx: APIRouteContext,
    *,
    path: str,
    body: dict[str, Any] | None,
) -> RouteResult:
    if body is None:
        return json_body_required_route_result(path=path)
    try:
        turn_payload = run_turn(
            config_path=ctx.config_path,
            payload=body,
            runtime=ctx.runtime,
            request_id=ctx.request_id,
        )
        status = HTTPStatus.OK
        payload = {"ok": True, "turn": turn_payload}
        session_id = str(turn_payload.get("session_id", "")).strip() or None
        run_id = str(turn_payload.get("run_id", "")).strip() or None
        return RouteResult(
            status=status,
            payload=payload,
            session_id=session_id,
            run_id=run_id,
        )
    except TurnRequestError as exc:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message=str(exc),
            details={"path": path},
            retryable=False,
        )
    except TurnTimeoutError as exc:
        return error_route_result(
            HTTPStatus.GATEWAY_TIMEOUT,
            code="turn_timeout",
            message=str(exc),
            details={"path": path},
            retryable=True,
            retry_after_ms=1000,
        )
    except Exception as exc:  # noqa: BLE001
        return exception_route_result(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="turn_failed",
            exc=exc,
            details={"path": path},
            retryable=False,
        )


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    if method_name == "POST" and path == "/v1/turn":
        return _handle_v1_turn(ctx=ctx, path=path, body=body, include_chunks=False)

    if method_name == "POST" and path == "/v1/turn/stream":
        return _handle_v1_turn(ctx=ctx, path=path, body=body, include_chunks=True)

    turn_input_result = handle_turn_inputs_request(
        ctx,
        method_name=method_name,
        path=path,
        body=body,
        query=query,
    )
    if turn_input_result is not None:
        return turn_input_result

    if (
        method_name == "POST"
        and (cancel_route := _CANCEL_RE.fullmatch(path)) is not None
    ):
        return _handle_cancel_turn(
            ctx,
            path=path,
            trace_id=unquote(cancel_route.group(1)),
            body=body,
        )

    if method_name == "POST" and path == "/turns":
        return _handle_legacy_turn_request(ctx, path=path, body=body)

    return None


def _handle_v1_turn(
    *,
    ctx: APIRouteContext,
    path: str,
    body: dict[str, Any] | None,
    include_chunks: bool,
) -> RouteResult:
    if body is None:
        return json_body_required_route_result(path=path)

    submission = None
    try:
        submission = open_turn_submission(
            config_path=ctx.config_path,
            runtime=ctx.runtime,
            body=body,
        )
        result_payload = collect_sync_turn_payload(
            submission,
            include_chunks=include_chunks,
            chunk_timeout_s=0.1,
        )
        payload = {
            "ok": True,
            **result_payload,
        }
        return RouteResult(
            status=HTTPStatus.OK,
            payload=payload,
            session_id=submission.session_id,
            run_id=submission.run_id,
        )
    except ValueError as exc:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message=str(exc),
            details={"path": path},
            retryable=False,
        )
    except TimeoutError as exc:
        return error_route_result(
            HTTPStatus.GATEWAY_TIMEOUT,
            code="turn_timeout",
            message=str(exc),
            details={"path": path},
            retryable=True,
            retry_after_ms=1000,
        )
    except RuntimeError as exc:
        return error_route_result(
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="runtime_unavailable",
            message=str(exc),
            details={"path": path},
            retryable=True,
            retry_after_ms=1000,
        )
    finally:
        if submission is not None:
            close_submission(submission)
