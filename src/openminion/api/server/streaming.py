"""Server-sent-event handling for streaming turn responses."""

import json
import logging
import re
from functools import partial
from http import HTTPStatus
from time import perf_counter
from typing import Any, Callable, cast
from urllib.parse import ParseResult, parse_qs, unquote

from openminion.api.core.turn_execution import (
    TurnSubmission,
    close_submission,
    open_turn_submission,
)
from openminion.api.core.deps import resolve_runtime_manager
from openminion.api.runtime import APIRuntime
from openminion.api.responses.serialization import (
    attach_response_meta,
    error_response,
    normalize_request_id,
)
from openminion.api.server.observability import (
    log_request_done,
    observe_request_metrics,
)
from openminion.services.runtime.daemon import turn_chunk_to_dict, turn_response_to_dict
from openminion.modules.runtime.contracts import TURN_STREAM_SCHEMA_VERSION


TURN_STREAM_CAPABILITIES = (
    "active_status_snapshot",
    "active_turn_replay",
    "sse_event_id",
)
_TURN_STREAM_ATTACH_RE = re.compile(r"/v1/turn/([^/]+)/stream")


def start_sse_stream_response(handler: Any, request_id: str | None) -> None:
    handler.send_response(int(HTTPStatus.OK))
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("X-Request-ID", normalize_request_id(request_id))
    handler.end_headers()


def write_sse_event(
    stream: Any,
    *,
    event: str,
    data: object,
    event_id: str | None = None,
) -> None:
    event_id_line = f"id: {event_id}\n" if event_id else ""
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    stream.write(f"{event_id_line}event: {event}\ndata: {encoded}\n\n".encode())
    stream.flush()


def try_handle_turn_stream_attach(
    handler: Any,
    *,
    parsed: ParseResult,
    request_id: str | None,
) -> bool:
    accept_header = (handler.headers.get("Accept") or "").lower()
    attach_route = _TURN_STREAM_ATTACH_RE.fullmatch(parsed.path)
    if attach_route is None or "text/event-stream" not in accept_header:
        return False
    trace_id = unquote(attach_route.group(1))
    query = parse_qs(parsed.query)
    after_values = query.get("after_sequence")
    try:
        cursor = parse_stream_cursor(
            trace_id=trace_id,
            last_event_id=handler.headers.get("Last-Event-ID"),
            after_sequence=after_values[0] if after_values else None,
        )
    except ValueError as exc:
        status, payload = error_response(
            HTTPStatus.BAD_REQUEST,
            code="invalid_stream_cursor",
            message=str(exc),
            details={"trace_id": trace_id},
            retryable=False,
        )
        handler._write_json(status, payload)
        return True

    handle_turn_stream_attach_request(
        trace_id=trace_id,
        after_sequence=cursor,
        request_id=request_id,
        config_path=handler.config_path,
        runtime=handler.runtime,
        start_sse_response=lambda: start_sse_stream_response(handler, request_id),
        write_sse_event=handler._write_sse_event,
        write_json=handler._write_json,
        observe_request_metrics=observe_request_metrics,
        log_request_done=log_request_done,
        perf_counter=perf_counter,
    )
    return True


def handle_http_turn_stream_request(
    handler: Any,
    *,
    body: dict[str, Any],
    request_id: str | None,
) -> None:
    handle_turn_stream_request(
        body=body,
        request_id=request_id,
        config_path=handler.config_path,
        runtime=handler.runtime,
        start_sse_response=lambda: start_sse_stream_response(handler, request_id),
        write_sse_event=handler._write_sse_event,
        write_json=handler._write_json,
        observe_request_metrics=observe_request_metrics,
        log_request_done=log_request_done,
        perf_counter=perf_counter,
    )


def _record_stream_response(
    *,
    status: HTTPStatus,
    payload: dict[str, Any],
    resolved_request_id: str,
    session_id_for_meta: str | None,
    run_id_for_meta: str | None,
    started_at: float,
    logger: logging.Logger,
    observe_request_metrics: Callable[..., int],
    log_request_done: Callable[..., None],
    method: str = "POST",
    path: str = "/v1/turn/stream",
    write_json: Callable[..., None] | None = None,
) -> dict[str, Any]:
    response = cast(
        dict[str, Any],
        attach_response_meta(
            payload,
            request_id=resolved_request_id,
            method=method,
            path=path,
            session_id=session_id_for_meta,
            run_id=run_id_for_meta,
        ),
    )
    duration_ms = observe_request_metrics(
        method=method,
        path=path,
        status=status,
        payload=response,
        started_at=started_at,
    )
    log_request_done(
        logger=logger,
        method=method,
        path=path,
        status=status,
        request_id=resolved_request_id,
        duration_ms=duration_ms,
        session_id=session_id_for_meta,
        run_id=run_id_for_meta,
    )
    if write_json is not None:
        write_json(status, response)
    return response


def _stream_error_payload(
    status: HTTPStatus,
    *,
    code: str,
    message: str,
    retryable: bool,
    retry_after_ms: int | None = None,
) -> tuple[HTTPStatus, dict[str, Any]]:
    return cast(
        tuple[HTTPStatus, dict[str, Any]],
        error_response(
            status,
            code=code,
            message=message,
            details={"path": "/v1/turn/stream"},
            retryable=retryable,
            retry_after_ms=retry_after_ms,
        ),
    )


def _open_stream_submission(
    *,
    body: dict[str, Any],
    config_path: str | None,
    runtime: APIRuntime | None,
) -> tuple[TurnSubmission | None, HTTPStatus | None, dict[str, Any] | None]:
    try:
        submission = open_turn_submission(
            config_path=config_path,
            runtime=runtime,
            body=body,
        )
    except ValueError as exc:
        status, payload = _stream_error_payload(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message=str(exc),
            retryable=False,
        )
        return None, status, payload
    except RuntimeError as exc:
        if getattr(exc, "code", "") == "SESSION_TURN_BUSY":
            retry_after_ms = max(1000, int(getattr(exc, "retry_after_s", 1)) * 1000)
            status, payload = _stream_error_payload(
                HTTPStatus.CONFLICT,
                code="SESSION_TURN_BUSY",
                message=str(exc),
                retryable=True,
                retry_after_ms=retry_after_ms,
            )
            payload["error"].setdefault("details", {})["retry_after_s"] = (
                retry_after_ms // 1000
            )
            return None, status, payload
        status, payload = _stream_error_payload(
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="runtime_unavailable",
            message=str(exc),
            retryable=True,
            retry_after_ms=1000,
        )
        return None, status, payload
    return submission, None, None


def _safe_stream_event(
    *,
    event: str,
    data: object,
    write_sse_event: Callable[..., None],
    event_id: str | None = None,
) -> bool:
    try:
        if event_id:
            write_sse_event(event=event, data=data, event_id=event_id)
        else:
            write_sse_event(event=event, data=data)
        return True
    except (BrokenPipeError, ConnectionResetError):
        return False


def _emit_stream_chunks(
    *,
    submission: TurnSubmission,
    run_id_for_meta: str | None,
    write_sse_event: Callable[..., None],
) -> bool:
    for chunk in submission.handle.stream(timeout_s=0.25):
        chunk_payload = turn_chunk_to_dict(chunk)
        chunk_payload.setdefault("trace_id", run_id_for_meta or "")
        chunk_payload.setdefault("kind", "progress")
        chunk_payload.setdefault("data", {})
        if not _safe_stream_event(
            event="chunk",
            data=chunk_payload,
            write_sse_event=write_sse_event,
            event_id=str(chunk_payload.get("event_id", "") or "") or None,
        ):
            return False
    return True


def _emit_error_outcome(
    *,
    status: HTTPStatus,
    payload: dict[str, Any],
    write_sse_event: Callable[..., None],
) -> tuple[HTTPStatus, dict[str, Any]]:
    _safe_stream_event(
        event="error",
        data=payload["error"],
        write_sse_event=write_sse_event,
    )
    return status, payload


def _result_exception_outcome(
    exc: Exception,
    *,
    write_sse_event: Callable[..., None],
) -> tuple[HTTPStatus, dict[str, Any]]:
    if isinstance(exc, TimeoutError):
        status, payload = error_response(
            HTTPStatus.GATEWAY_TIMEOUT,
            code="turn_timeout",
            message=str(exc),
            retryable=True,
        )
    elif getattr(exc, "code", "") == "SESSION_TURN_BUSY":
        retry_after_ms = max(1000, int(getattr(exc, "retry_after_s", 1)) * 1000)
        status, payload = error_response(
            HTTPStatus.CONFLICT,
            code="SESSION_TURN_BUSY",
            message=str(exc),
            details={"retry_after_s": retry_after_ms // 1000},
            retryable=True,
            retry_after_ms=retry_after_ms,
        )
    else:
        status, payload = error_response(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="turn_failed",
            message=str(exc),
            retryable=False,
        )
    return _emit_error_outcome(
        status=status,
        payload=payload,
        write_sse_event=write_sse_event,
    )


def _turn_response_outcome(
    turn_response: Any,
    *,
    run_id_for_meta: str | None,
    client_disconnected: bool,
    write_sse_event: Callable[..., None],
) -> tuple[HTTPStatus, dict[str, Any]]:

    response_payload = turn_response_to_dict(turn_response)
    response_payload.setdefault("final_text", "")
    if not client_disconnected:
        _safe_stream_event(
            event="response",
            data={
                "trace_id": run_id_for_meta,
                **response_payload,
            },
            write_sse_event=write_sse_event,
        )
    errors = response_payload.get("errors")
    if isinstance(errors, list) and errors:
        raw_error = errors[0]
        first_error = dict(raw_error) if isinstance(raw_error, dict) else {}
        status, payload = error_response(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            error=first_error,
            retryable=bool(first_error.get("retryable", False)),
        )
        if not client_disconnected:
            return _emit_error_outcome(
                status=status,
                payload=payload,
                write_sse_event=write_sse_event,
            )
        return status, payload
    return HTTPStatus.OK, {"ok": True}


def _collect_handle_result(
    *,
    handle: Any,
    timeout_s: float,
    run_id_for_meta: str | None,
    client_disconnected: bool,
    write_sse_event: Callable[..., None],
) -> tuple[HTTPStatus, dict[str, Any]]:
    try:
        turn_response = handle.result(timeout_s=max(0.0, float(timeout_s)))
    except Exception as exc:  # noqa: BLE001
        return _result_exception_outcome(exc, write_sse_event=write_sse_event)
    return _turn_response_outcome(
        turn_response,
        run_id_for_meta=run_id_for_meta,
        client_disconnected=client_disconnected,
        write_sse_event=write_sse_event,
    )


def _stream_meta(
    *,
    request_id: str,
    trace_id: str,
    session_id: str | None,
    replay_floor_sequence: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "trace_id": trace_id,
        "session_id": session_id,
        "stream_schema_version": TURN_STREAM_SCHEMA_VERSION,
        "capabilities": list(TURN_STREAM_CAPABILITIES),
    }
    if replay_floor_sequence is not None:
        payload["replay_floor_sequence"] = replay_floor_sequence
    return payload


def parse_stream_cursor(
    *,
    trace_id: str,
    last_event_id: str | None,
    after_sequence: str | None,
) -> int:
    raw_sequence = str(after_sequence or "").strip()
    if not raw_sequence and last_event_id:
        event_trace_id, separator, event_sequence = str(last_event_id).rpartition(":")
        if separator and event_trace_id == trace_id:
            raw_sequence = event_sequence
    if not raw_sequence:
        return 0
    sequence = int(raw_sequence)
    if sequence < 0:
        raise ValueError("stream cursor must be zero or greater")
    return sequence


def handle_turn_stream_request(
    *,
    body: dict[str, Any],
    request_id: str | None,
    config_path: str | None,
    runtime: APIRuntime | None,
    start_sse_response: Callable[[], None],
    write_sse_event: Callable[..., None],
    write_json: Callable[..., None],
    observe_request_metrics: Callable[..., int],
    log_request_done: Callable[..., None],
    perf_counter: Callable[[], float],
) -> None:
    resolved_request_id = normalize_request_id(request_id)
    started_at = perf_counter()
    logger = logging.getLogger("openminion.api")

    submission, error_status, error_payload = _open_stream_submission(
        body=body,
        config_path=config_path,
        runtime=runtime,
    )
    if submission is None:
        assert error_status is not None and error_payload is not None
        _record_stream_response(
            status=error_status,
            payload=error_payload,
            resolved_request_id=resolved_request_id,
            session_id_for_meta=None,
            run_id_for_meta=None,
            started_at=started_at,
            logger=logger,
            observe_request_metrics=observe_request_metrics,
            log_request_done=log_request_done,
            write_json=write_json,
        )
        return

    session_id_for_meta = submission.session_id
    run_id_for_meta = submission.run_id
    client_disconnected = False

    try:
        start_sse_response()
        if not _safe_stream_event(
            event="meta",
            data=_stream_meta(
                request_id=resolved_request_id,
                trace_id=run_id_for_meta,
                session_id=session_id_for_meta,
            ),
            write_sse_event=write_sse_event,
        ):
            client_disconnected = True
        if not client_disconnected:
            client_disconnected = not _emit_stream_chunks(
                submission=submission,
                run_id_for_meta=run_id_for_meta,
                write_sse_event=write_sse_event,
            )
        status_for_metrics, payload_for_metrics = _collect_handle_result(
            handle=submission.handle,
            timeout_s=submission.timeout_s,
            run_id_for_meta=run_id_for_meta,
            client_disconnected=client_disconnected,
            write_sse_event=write_sse_event,
        )
        _safe_stream_event(
            event="done",
            data={
                "trace_id": run_id_for_meta,
                "status": "complete"
                if status_for_metrics == HTTPStatus.OK
                else "error",
                "stream_schema_version": TURN_STREAM_SCHEMA_VERSION,
            },
            write_sse_event=write_sse_event,
        )
    finally:
        close_submission(submission)

    _record_stream_response(
        status=status_for_metrics,
        payload=payload_for_metrics,
        resolved_request_id=resolved_request_id,
        session_id_for_meta=session_id_for_meta,
        run_id_for_meta=run_id_for_meta,
        started_at=started_at,
        logger=logger,
        observe_request_metrics=observe_request_metrics,
        log_request_done=log_request_done,
    )


def _stream_attached_turn(
    *,
    handle: Any,
    trace_id: str,
    after_sequence: int,
    resolved_request_id: str,
    start_sse_response: Callable[[], None],
    write_sse_event: Callable[..., None],
) -> tuple[HTTPStatus, dict[str, Any]]:
    start_sse_response()
    connected = _safe_stream_event(
        event="meta",
        data=_stream_meta(
            request_id=resolved_request_id,
            trace_id=trace_id,
            session_id=handle.session_id or None,
            replay_floor_sequence=handle.replay_floor_sequence,
        ),
        write_sse_event=write_sse_event,
    )
    if connected:
        for chunk in handle.subscribe(after_sequence=after_sequence, timeout_s=0.25):
            if not _safe_stream_event(
                event="chunk",
                data=turn_chunk_to_dict(chunk),
                event_id=chunk.event_id,
                write_sse_event=write_sse_event,
            ):
                connected = False
                break
    if not connected:
        return cast(
            tuple[HTTPStatus, dict[str, Any]],
            error_response(
                HTTPStatus.OK,
                code="client_disconnected",
                message="stream client disconnected",
                retryable=True,
            ),
        )

    status, payload = _collect_handle_result(
        handle=handle,
        timeout_s=0.0,
        run_id_for_meta=trace_id,
        client_disconnected=False,
        write_sse_event=write_sse_event,
    )
    _safe_stream_event(
        event="done",
        data={
            "trace_id": trace_id,
            "status": "complete" if status == HTTPStatus.OK else "error",
            "stream_schema_version": TURN_STREAM_SCHEMA_VERSION,
        },
        write_sse_event=write_sse_event,
    )
    return status, payload


def handle_turn_stream_attach_request(
    *,
    trace_id: str,
    after_sequence: int,
    request_id: str | None,
    config_path: str | None,
    runtime: APIRuntime | None,
    start_sse_response: Callable[[], None],
    write_sse_event: Callable[..., None],
    write_json: Callable[..., None],
    observe_request_metrics: Callable[..., int],
    log_request_done: Callable[..., None],
    perf_counter: Callable[[], float],
) -> None:
    resolved_request_id = normalize_request_id(request_id)
    started_at = perf_counter()
    path = "/v1/turn/:trace_id/stream"
    logger = logging.getLogger("openminion.api")
    active_runtime: APIRuntime | None = None
    own_runtime = False
    record_response = partial(
        _record_stream_response,
        resolved_request_id=resolved_request_id,
        run_id_for_meta=trace_id,
        started_at=started_at,
        logger=logger,
        observe_request_metrics=observe_request_metrics,
        log_request_done=log_request_done,
        method="GET",
        path=path,
    )
    try:
        manager, active_runtime, own_runtime = resolve_runtime_manager(
            config_path=config_path,
            runtime=runtime,
        )
        handle = cast(Any, manager).get_turn_handle(trace_id)
    except (OSError, RuntimeError, ValueError) as exc:
        if own_runtime and active_runtime is not None:
            active_runtime.close()
        status, payload = error_response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="runtime_unavailable",
            message=str(exc),
            details={"trace_id": trace_id},
            retryable=True,
            retry_after_ms=1000,
        )
        record_response(
            status=status,
            payload=payload,
            session_id_for_meta=None,
            write_json=write_json,
        )
        return

    try:
        if handle is None:
            status, payload = error_response(
                HTTPStatus.NOT_FOUND,
                code="trace_not_found",
                message=f"Active trace not found: {trace_id}",
                details={"trace_id": trace_id},
                retryable=False,
            )
            record_response(
                status=status,
                payload=payload,
                session_id_for_meta=None,
                write_json=write_json,
            )
            return

        status, payload = _stream_attached_turn(
            handle=handle,
            trace_id=trace_id,
            after_sequence=after_sequence,
            resolved_request_id=resolved_request_id,
            start_sse_response=start_sse_response,
            write_sse_event=write_sse_event,
        )
        record_response(
            status=status,
            payload=payload,
            session_id_for_meta=handle.session_id or None,
        )
    finally:
        if own_runtime and active_runtime is not None:
            active_runtime.close()


__all__ = [
    "handle_http_turn_stream_request",
    "handle_turn_stream_attach_request",
    "handle_turn_stream_request",
    "parse_stream_cursor",
    "start_sse_stream_response",
    "try_handle_turn_stream_attach",
    "write_sse_event",
]
