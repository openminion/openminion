"""Turn-input queue route handlers."""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import Any, cast
from urllib.parse import parse_qs, unquote

from openminion.api.config import close_api_runtime_if_owned, resolve_api_runtime
from openminion.api.core.deps import resolve_runtime_manager
from openminion.api.queries.sessions import append_session_event
from openminion.api.runtime import APIRuntime
from openminion.services.runtime.turn_input import (
    QUEUE_EVENT_CANCEL_ACKNOWLEDGED,
    QUEUE_EVENT_CANCEL_FAILED,
    QUEUE_EVENT_CANCEL_REQUESTED,
    QUEUE_EVENT_DROPPED,
    QUEUE_EVENT_ENQUEUED,
    QUEUE_EVENT_MOVED,
    QUEUE_EVENT_STEER_DEFERRED,
    TurnInputIntent,
    TurnInputQueue,
    TurnInputQueueEntry,
    TurnInputQueueError,
    TurnInputQueueStatus,
)

from .base import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    json_body_required_route_result,
    runtime_unavailable_route_result,
)

_CANCEL_AND_RUN_NEXT_RE = re.compile(r"/v1/turn/([^/]+)/cancel-and-run-next")
_TURN_INPUTS_RE = re.compile(r"/v1/sessions/([^/]+)/turn-inputs/?")
_TURN_INPUT_RE = re.compile(r"/v1/sessions/([^/]+)/turn-inputs/([^/]+)/?")
_TURN_INPUT_MOVE_RE = re.compile(r"/v1/sessions/([^/]+)/turn-inputs/([^/]+)/move/?")


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    if (
        method_name == "POST"
        and (cancel_next_route := _CANCEL_AND_RUN_NEXT_RE.fullmatch(path)) is not None
    ):
        return _handle_cancel_and_run_next(
            ctx,
            path=path,
            trace_id=unquote(cancel_next_route.group(1)),
            body=body,
        )

    if (turn_inputs_route := _TURN_INPUTS_RE.fullmatch(path)) is not None:
        session_id = unquote(turn_inputs_route.group(1))
        if method_name == "POST":
            return _handle_enqueue_turn_input(
                ctx,
                path=path,
                session_id=session_id,
                body=body,
            )
        if method_name == "GET":
            return _handle_list_turn_inputs(
                ctx,
                path=path,
                session_id=session_id,
                query=query,
            )

    if (
        method_name == "POST"
        and (move_route := _TURN_INPUT_MOVE_RE.fullmatch(path)) is not None
    ):
        return _handle_move_turn_input(
            ctx,
            path=path,
            session_id=unquote(move_route.group(1)),
            queue_id=unquote(move_route.group(2)),
            body=body,
        )

    if (
        method_name == "DELETE"
        and (entry_route := _TURN_INPUT_RE.fullmatch(path)) is not None
    ):
        return _handle_drop_turn_input(
            ctx,
            path=path,
            session_id=unquote(entry_route.group(1)),
            queue_id=unquote(entry_route.group(2)),
            body=body,
        )
    return None


def _entry_payload(entry: TurnInputQueueEntry) -> dict[str, Any]:
    return entry.to_dict(include_text=True)


def _turn_input_queue(runtime: object) -> TurnInputQueue:
    queue = getattr(runtime, "turn_input_queue", None)
    if isinstance(queue, TurnInputQueue):
        return queue
    queue = TurnInputQueue()
    setattr(runtime, "turn_input_queue", queue)
    return queue


def _queue_error_result(exc: TurnInputQueueError) -> RouteResult:
    status = HTTPStatus.BAD_REQUEST
    if exc.code == "QUEUE_FULL":
        status = HTTPStatus.TOO_MANY_REQUESTS
    elif exc.code == "QUEUE_ENTRY_NOT_FOUND":
        status = HTTPStatus.NOT_FOUND
    elif exc.code == "QUEUE_CONFLICT":
        status = HTTPStatus.CONFLICT
    return error_route_result(
        status,
        code=exc.code,
        message=str(exc),
        details=exc.details,
        retryable=False,
    )


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _required_int(value, field_name)


def _required_int(value: Any, field_name: str) -> int:
    if value is None:
        raise ValueError(f"{field_name} is required.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer.") from exc


def _append_turn_input_event(
    *,
    ctx: APIRouteContext,
    runtime: APIRuntime,
    session_id: str,
    event_type: str,
    entry: TurnInputQueueEntry | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    event_payload = dict(payload or {})
    if entry is not None:
        event_payload.update(entry.event_payload())
    try:
        append_session_event(
            config_path=ctx.config_path,
            session_id=session_id,
            event_type=event_type,
            payload=event_payload,
            runtime=runtime,
        )
    except Exception:
        pass


def _resolve_runtime_for_queue(
    ctx: APIRouteContext,
    *,
    path: str,
) -> tuple[APIRuntime | None, bool, RouteResult | None]:
    try:
        runtime, own_runtime = resolve_api_runtime(
            config_path=ctx.config_path,
            runtime=ctx.runtime,
        )
        return runtime, own_runtime, None
    except Exception as exc:  # noqa: BLE001
        return None, False, runtime_unavailable_route_result(path=path, exc=exc)


def _handle_enqueue_turn_input(
    ctx: APIRouteContext,
    *,
    path: str,
    session_id: str,
    body: dict[str, Any] | None,
) -> RouteResult:
    if body is None:
        return json_body_required_route_result(path=path, session_id=session_id)
    runtime, own_runtime, error = _resolve_runtime_for_queue(ctx, path=path)
    if error is not None or runtime is None:
        return error or runtime_unavailable_route_result(
            path=path, exc="runtime unavailable"
        )
    try:
        requested_intent = str(body.get("intent", TurnInputIntent.QUEUE_NEXT.value))
        metadata = dict(body.get("metadata") or {})
        intent = TurnInputIntent(requested_intent)
        if intent == TurnInputIntent.STEER_CURRENT:
            metadata.setdefault("requested_intent", intent.value)
            metadata.setdefault("steer_status", "steer_deferred")
            intent = TurnInputIntent.QUEUE_NEXT
        entry = _turn_input_queue(runtime).enqueue(
            session_id=session_id,
            agent_id=str(body.get("agent_id", "")).strip(),
            text=str(body.get("text", "")),
            intent=intent,
            source_client=str(body.get("source_client", "api")),
            idempotency_key=body.get("idempotency_key"),
            priority=int(body.get("priority", 0) or 0),
            metadata=metadata,
        )
        if requested_intent == TurnInputIntent.STEER_CURRENT.value:
            _append_turn_input_event(
                ctx=ctx,
                runtime=runtime,
                session_id=session_id,
                event_type=QUEUE_EVENT_STEER_DEFERRED,
                entry=entry,
            )
        _append_turn_input_event(
            ctx=ctx,
            runtime=runtime,
            session_id=session_id,
            event_type=QUEUE_EVENT_ENQUEUED,
            entry=entry,
        )
        return RouteResult(
            status=HTTPStatus.ACCEPTED,
            payload={"ok": True, "entry": _entry_payload(entry)},
            session_id=session_id,
        )
    except TurnInputQueueError as exc:
        return _queue_error_result(exc)
    except ValueError as exc:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message=str(exc),
            details={"path": path},
            retryable=False,
        )
    finally:
        close_api_runtime_if_owned(runtime, own_runtime=own_runtime)


def _handle_list_turn_inputs(
    ctx: APIRouteContext,
    *,
    path: str,
    session_id: str,
    query: str | None,
) -> RouteResult:
    runtime, own_runtime, error = _resolve_runtime_for_queue(ctx, path=path)
    if error is not None or runtime is None:
        return error or runtime_unavailable_route_result(
            path=path, exc="runtime unavailable"
        )
    try:
        params = parse_qs(query or "")
        agent_id = (params.get("agent_id") or [""])[0].strip() or None
        statuses = {
            TurnInputQueueStatus(value)
            for value in params.get("status", [])
            if str(value or "").strip()
        }
        entries = _turn_input_queue(runtime).list_entries(
            session_id=session_id,
            agent_id=agent_id,
            statuses=statuses or None,
        )
        return RouteResult(
            status=HTTPStatus.OK,
            payload={
                "ok": True,
                "entries": [_entry_payload(entry) for entry in entries],
            },
            session_id=session_id,
        )
    except TurnInputQueueError as exc:
        return _queue_error_result(exc)
    except ValueError as exc:
        return error_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message=str(exc),
            details={"path": path},
            retryable=False,
        )
    finally:
        close_api_runtime_if_owned(runtime, own_runtime=own_runtime)


def _handle_drop_turn_input(
    ctx: APIRouteContext,
    *,
    path: str,
    session_id: str,
    queue_id: str,
    body: dict[str, Any] | None,
) -> RouteResult:
    runtime, own_runtime, error = _resolve_runtime_for_queue(ctx, path=path)
    if error is not None or runtime is None:
        return error or runtime_unavailable_route_result(
            path=path, exc="runtime unavailable"
        )
    try:
        entry = _turn_input_queue(runtime).drop(
            session_id=session_id,
            queue_id=queue_id,
            status_version=_optional_int(
                None if body is None else body.get("status_version"),
                "status_version",
            ),
        )
        _append_turn_input_event(
            ctx=ctx,
            runtime=runtime,
            session_id=session_id,
            event_type=QUEUE_EVENT_DROPPED,
            entry=entry,
        )
        return RouteResult(
            status=HTTPStatus.OK,
            payload={"ok": True, "entry": _entry_payload(entry)},
            session_id=session_id,
        )
    except ValueError as exc:
        return _invalid_request(path=path, exc=exc)
    except TurnInputQueueError as exc:
        return _queue_error_result(exc)
    finally:
        close_api_runtime_if_owned(runtime, own_runtime=own_runtime)


def _handle_move_turn_input(
    ctx: APIRouteContext,
    *,
    path: str,
    session_id: str,
    queue_id: str,
    body: dict[str, Any] | None,
) -> RouteResult:
    if body is None:
        return json_body_required_route_result(path=path, session_id=session_id)
    runtime, own_runtime, error = _resolve_runtime_for_queue(ctx, path=path)
    if error is not None or runtime is None:
        return error or runtime_unavailable_route_result(
            path=path, exc="runtime unavailable"
        )
    try:
        entry = _turn_input_queue(runtime).move(
            session_id=session_id,
            queue_id=queue_id,
            status_version=_required_int(body.get("status_version"), "status_version"),
            before_queue_id=str(body.get("before_queue_id", "")).strip() or None,
            after_queue_id=str(body.get("after_queue_id", "")).strip() or None,
        )
        _append_turn_input_event(
            ctx=ctx,
            runtime=runtime,
            session_id=session_id,
            event_type=QUEUE_EVENT_MOVED,
            entry=entry,
            payload={
                "before_queue_id": str(body.get("before_queue_id", "")).strip(),
                "after_queue_id": str(body.get("after_queue_id", "")).strip(),
            },
        )
        return RouteResult(
            status=HTTPStatus.OK,
            payload={"ok": True, "entry": _entry_payload(entry)},
            session_id=session_id,
        )
    except ValueError as exc:
        return _invalid_request(path=path, exc=exc)
    except TurnInputQueueError as exc:
        return _queue_error_result(exc)
    finally:
        close_api_runtime_if_owned(runtime, own_runtime=own_runtime)


def _handle_cancel_and_run_next(
    ctx: APIRouteContext,
    *,
    path: str,
    trace_id: str,
    body: dict[str, Any] | None,
) -> RouteResult:
    if body is None:
        return json_body_required_route_result(path=path)
    try:
        manager, active_runtime, own_runtime = resolve_runtime_manager(
            config_path=ctx.config_path,
            runtime=ctx.runtime,
        )
    except Exception as exc:  # noqa: BLE001
        return runtime_unavailable_route_result(path=path, exc=exc)
    session_id = str(body.get("session_id", "")).strip()
    agent_id = str(body.get("agent_id", "")).strip()
    try:
        reserved = _turn_input_queue(active_runtime).reserve_next(
            session_id=session_id,
            agent_id=agent_id,
            expected_queue_id=str(body.get("expected_queue_id", "")).strip() or None,
        )
        if reserved is None:
            return error_route_result(
                HTTPStatus.CONFLICT,
                code="QUEUE_CONFLICT",
                message="No queued entry is available to reserve.",
                details={"session_id": session_id, "agent_id": agent_id},
                retryable=False,
            )
        _append_turn_input_event(
            ctx=ctx,
            runtime=active_runtime,
            session_id=session_id,
            event_type=QUEUE_EVENT_CANCEL_REQUESTED,
            entry=reserved,
            payload={"trace_id": trace_id},
        )
        cancelled = bool(cast(Any, manager).cancel_turn(trace_id))
        event_type = (
            QUEUE_EVENT_CANCEL_ACKNOWLEDGED if cancelled else QUEUE_EVENT_CANCEL_FAILED
        )
        _append_turn_input_event(
            ctx=ctx,
            runtime=active_runtime,
            session_id=session_id,
            event_type=event_type,
            entry=reserved,
            payload={"trace_id": trace_id},
        )
        if not cancelled:
            return error_route_result(
                HTTPStatus.NOT_FOUND,
                code="trace_not_found",
                message=f"Trace not found: {trace_id}",
                details={"trace_id": trace_id},
                retryable=False,
            )
        return RouteResult(
            status=HTTPStatus.ACCEPTED,
            payload={
                "ok": True,
                "trace_id": trace_id,
                "cancelled": True,
                "reserved_entry": _entry_payload(reserved),
            },
            session_id=session_id,
        )
    except TurnInputQueueError as exc:
        return _queue_error_result(exc)
    finally:
        if own_runtime:
            active_runtime.close()


def _invalid_request(*, path: str, exc: ValueError) -> RouteResult:
    return error_route_result(
        HTTPStatus.BAD_REQUEST,
        code="invalid_request",
        message=str(exc),
        details={"path": path},
        retryable=False,
    )


__all__ = ["handle_request"]
