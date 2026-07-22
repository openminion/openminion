"""Server-sent-event handling for streaming turn responses."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Callable

from openminion.api.core.turn_execution import close_submission, open_turn_submission
from openminion.api.runtime import APIRuntime
from openminion.api.responses.serialization import (
    attach_response_meta,
    error_response,
    normalize_request_id,
)
from openminion.services.runtime.daemon import turn_chunk_to_dict, turn_response_to_dict


def _record_stream_response(
    *,
    status: HTTPStatus,
    payload: dict,
    resolved_request_id: str,
    session_id_for_meta: str | None,
    run_id_for_meta: str | None,
    started_at: float,
    logger: logging.Logger,
    observe_request_metrics: Callable[..., int],
    log_request_done: Callable[..., None],
    write_json: Callable[..., None] | None = None,
) -> dict:
    response = attach_response_meta(
        payload,
        request_id=resolved_request_id,
        method="POST",
        path="/v1/turn/stream",
        session_id=session_id_for_meta,
        run_id=run_id_for_meta,
    )
    duration_ms = observe_request_metrics(
        method="POST",
        path="/v1/turn/stream",
        status=status,
        payload=response,
        started_at=started_at,
    )
    log_request_done(
        logger=logger,
        method="POST",
        path="/v1/turn/stream",
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
) -> tuple[HTTPStatus, dict]:
    return error_response(
        status,
        code=code,
        message=message,
        details={"path": "/v1/turn/stream"},
        retryable=retryable,
        retry_after_ms=retry_after_ms,
    )


def _open_stream_submission(
    *,
    body: dict,
    config_path: str | None,
    runtime: APIRuntime | None,
) -> tuple[object | None, HTTPStatus | None, dict | None]:
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
) -> bool:
    try:
        write_sse_event(event=event, data=data)
        return True
    except (BrokenPipeError, ConnectionResetError):
        return False


def _emit_stream_chunks(
    *,
    submission,
    run_id_for_meta: str | None,
    write_sse_event: Callable[..., None],
) -> bool:
    for chunk in submission.handle.stream(timeout_s=0.25):
        chunk_payload = turn_chunk_to_dict(chunk)
        if not isinstance(chunk_payload, dict):
            chunk_payload = {}
        chunk_payload.setdefault("trace_id", run_id_for_meta or "")
        chunk_payload.setdefault("kind", "progress")
        chunk_payload.setdefault("data", {})
        if not _safe_stream_event(
            event="chunk",
            data=chunk_payload,
            write_sse_event=write_sse_event,
        ):
            return False
    return True


def _collect_stream_result(
    *,
    submission,
    run_id_for_meta: str | None,
    client_disconnected: bool,
    write_sse_event: Callable[..., None],
) -> tuple[HTTPStatus, dict]:
    try:
        turn_response = submission.handle.result(
            timeout_s=max(0.0, float(submission.timeout_s))
        )
    except TimeoutError as exc:
        payload = {
            "ok": False,
            "error": {
                "code": "turn_timeout",
                "message": str(exc),
                "retryable": True,
            },
        }
        _safe_stream_event(
            event="error",
            data=payload["error"],
            write_sse_event=write_sse_event,
        )
        return HTTPStatus.GATEWAY_TIMEOUT, payload
    except RuntimeError as exc:
        if getattr(exc, "code", "") == "SESSION_TURN_BUSY":
            retry_after_ms = max(1000, int(getattr(exc, "retry_after_s", 1)) * 1000)
            payload = {
                "ok": False,
                "error": {
                    "code": "SESSION_TURN_BUSY",
                    "message": str(exc),
                    "retryable": True,
                    "retry_after_ms": retry_after_ms,
                    "details": {"retry_after_s": retry_after_ms // 1000},
                },
            }
            _safe_stream_event(
                event="error",
                data=payload["error"],
                write_sse_event=write_sse_event,
            )
            return HTTPStatus.CONFLICT, payload
        payload = {
            "ok": False,
            "error": {
                "code": "turn_failed",
                "message": str(exc),
                "retryable": False,
            },
        }
        _safe_stream_event(
            event="error",
            data=payload["error"],
            write_sse_event=write_sse_event,
        )
        return HTTPStatus.INTERNAL_SERVER_ERROR, payload
    except Exception as exc:  # noqa: BLE001
        payload = {
            "ok": False,
            "error": {
                "code": "turn_failed",
                "message": str(exc),
                "retryable": False,
            },
        }
        _safe_stream_event(
            event="error",
            data=payload["error"],
            write_sse_event=write_sse_event,
        )
        return HTTPStatus.INTERNAL_SERVER_ERROR, payload

    if not client_disconnected:
        response_payload = turn_response_to_dict(turn_response)
        if not isinstance(response_payload, dict):
            response_payload = {}
        response_payload.setdefault("final_text", "")
        _safe_stream_event(
            event="response",
            data={
                "trace_id": run_id_for_meta,
                **response_payload,
            },
            write_sse_event=write_sse_event,
        )
    return HTTPStatus.OK, {"ok": True}


def handle_turn_stream_request(
    *,
    body: dict,
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
    session_id_for_meta: str | None = None
    run_id_for_meta: str | None = None
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
            session_id_for_meta=session_id_for_meta,
            run_id_for_meta=run_id_for_meta,
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
            data={
                "request_id": resolved_request_id,
                "trace_id": run_id_for_meta,
                "session_id": session_id_for_meta,
            },
            write_sse_event=write_sse_event,
        ):
            client_disconnected = True
        if not client_disconnected:
            client_disconnected = not _emit_stream_chunks(
                submission=submission,
                run_id_for_meta=run_id_for_meta,
                write_sse_event=write_sse_event,
            )
        status_for_metrics, payload_for_metrics = _collect_stream_result(
            submission=submission,
            run_id_for_meta=run_id_for_meta,
            client_disconnected=client_disconnected,
            write_sse_event=write_sse_event,
        )
        _safe_stream_event(
            event="done",
            data={
                "status": "complete" if status_for_metrics == HTTPStatus.OK else "error"
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


__all__ = ["handle_turn_stream_request"]
