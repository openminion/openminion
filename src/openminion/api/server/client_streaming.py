"""Server-sent-event handling for streaming turn responses."""

import logging
import json
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Callable, cast
from uuid import UUID

from openminion.api.core.turn_execution import (
    TurnSubmission,
    close_submission,
    open_turn_submission,
)
from openminion.api.runtime import APIRuntime
from openminion.api.responses.serialization import (
    attach_response_meta,
    error_response,
    normalize_request_id,
)
from openminion.services.runtime.daemon import turn_chunk_to_dict, turn_response_to_dict

if TYPE_CHECKING:
    from openminion.api.server.client_auth import ClientIdentity
    from openminion.api.server.client_media import ClientMediaCoordinator
    from openminion.services.runtime.interfaces import DesktopApprovalRequester


_DESKTOP_TURN_REQUIRED_KEYS = {
    "trace_id",
    "session_id",
    "agent_id",
    "input_text",
    "mode",
    "stream",
    "channel",
    "user",
    "idempotency_key",
}
_DESKTOP_TURN_KEYS = _DESKTOP_TURN_REQUIRED_KEYS | {"attachments"}
_MEDIA_ID = re.compile(r"[0-9a-f]{48}")
_DESKTOP_SSE_EVENT_LIMIT = 256 * 1024


class _StreamLimitExceeded(RuntimeError):
    pass


def _record_client_stream_response(
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
    write_json: Callable[..., None] | None = None,
) -> dict[str, Any]:
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
    return cast(dict[str, Any], response)


def _client_stream_error_payload(
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


def _open_client_stream_submission(
    *,
    body: dict[str, Any],
    config_path: str | None,
    runtime: APIRuntime | None,
    desktop_approval_requester: "DesktopApprovalRequester | None" = None,
    resolved_attachment_refs: tuple[str, ...] = (),
) -> tuple[TurnSubmission | None, HTTPStatus | None, dict[str, Any] | None]:
    try:
        submission = open_turn_submission(
            config_path=config_path,
            runtime=runtime,
            body=body,
            desktop_approval_requester=desktop_approval_requester,
            resolved_attachment_refs=resolved_attachment_refs,
        )
    except ValueError as exc:
        status, payload = _client_stream_error_payload(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message=str(exc),
            retryable=False,
        )
        return None, status, payload
    except RuntimeError as exc:
        if getattr(exc, "code", "") == "SESSION_TURN_BUSY":
            retry_after_ms = max(1000, int(getattr(exc, "retry_after_s", 1)) * 1000)
            status, payload = _client_stream_error_payload(
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
        status, payload = _client_stream_error_payload(
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="runtime_unavailable",
            message=str(exc),
            retryable=True,
            retry_after_ms=1000,
        )
        return None, status, payload
    return submission, None, None


def _safe_client_stream_event(
    *,
    event: str,
    data: object,
    write_sse_event: Callable[..., None],
    max_event_bytes: int | None = None,
) -> bool:
    if max_event_bytes is not None:
        encoded = (
            f"event: {event}\ndata: "
            f"{json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
        ).encode("utf-8")
        if len(encoded) > max_event_bytes:
            raise _StreamLimitExceeded(event)
    try:
        write_sse_event(event=event, data=data)
        return True
    except (BrokenPipeError, ConnectionResetError):
        return False


def _emit_client_stream_chunks(
    *,
    submission: TurnSubmission,
    run_id_for_meta: str | None,
    write_sse_event: Callable[..., None],
    max_event_bytes: int | None = None,
    desktop_client: bool = False,
) -> bool:
    for chunk in submission.handle.stream(timeout_s=0.25):
        chunk_payload = turn_chunk_to_dict(chunk)
        chunk_payload.setdefault("trace_id", run_id_for_meta or "")
        chunk_payload.setdefault("kind", "progress")
        chunk_payload.setdefault("data", {})
        if desktop_client:
            projected = _desktop_chunk(chunk_payload)
            if projected is None:
                continue
            chunk_payload = projected
        if not _safe_client_stream_event(
            event="chunk",
            data=chunk_payload,
            write_sse_event=write_sse_event,
            max_event_bytes=max_event_bytes,
        ):
            return False
    return True


def _desktop_chunk(chunk: dict[str, Any]) -> dict[str, Any] | None:
    trace_id = _bounded_text(chunk.get("trace_id"), 256)
    kind = str(chunk.get("kind", "") or "")
    data = chunk.get("data")
    timestamp = _bounded_text(chunk.get("ts"), 128)
    if trace_id is None or timestamp is None or not isinstance(data, dict):
        return None
    projected: dict[str, Any] | None
    if kind == "status":
        status_key = _bounded_text(data.get("status_key"), 256)
        if status_key is None:
            return None
        projected = {
            "status_key": status_key,
            "label": _optional_bounded_text(data.get("label"), 4 * 1024),
            "detail_text": _optional_bounded_text(data.get("detail_text"), 4 * 1024),
        }
    elif kind == "final_text":
        text = _bounded_text(data.get("text"), 256 * 1024)
        if text is None:
            return None
        projected = {"text": text}
    elif kind == "tool_started":
        tool_name = _bounded_text(data.get("tool_name"), 256)
        call_id = _bounded_text(data.get("call_id"), 256)
        state = _bounded_text(data.get("state"), 256)
        if None in {tool_name, call_id, state}:
            return None
        projected = {"tool_name": tool_name, "call_id": call_id, "state": state}
    elif kind == "tool_completed":
        tool_name = _bounded_text(data.get("tool_name"), 256)
        call_id = _bounded_text(data.get("call_id"), 256)
        state = _bounded_text(data.get("state"), 256)
        duration_ms = data.get("duration_ms")
        exit_code = data.get("exit_code")
        if (
            None in {tool_name, call_id, state}
            or type(data.get("ok")) is not bool
            or (
                duration_ms is not None
                and (type(duration_ms) is not int or duration_ms < 0)
            )
            or (exit_code is not None and type(exit_code) is not int)
        ):
            return None
        projected = {
            "tool_name": tool_name,
            "call_id": call_id,
            "state": state,
            "ok": data["ok"],
            "duration_ms": duration_ms,
            "exit_code": exit_code,
        }
    elif kind == "approval_required":
        projected = _desktop_approval_required(data)
        if not projected:
            return None
    elif kind == "approval_resolved":
        projected = _desktop_approval_resolved(data)
        if not projected:
            return None
    else:
        return None
    return {"trace_id": trace_id, "kind": kind, "data": projected, "ts": timestamp}


def _desktop_approval_required(data: dict[str, Any]) -> dict[str, Any] | None:
    keys = data.get("argument_keys")
    fields = {
        name: _bounded_text(
            data.get(name), 256 if name not in {"requested_at", "expires_at"} else 128
        )
        for name in (
            "approval_id",
            "call_id",
            "tool_name",
            "requested_at",
            "expires_at",
        )
    }
    if any(value is None for value in fields.values()) or not _valid_argument_keys(
        keys
    ):
        return None
    assert isinstance(keys, list)
    return {**fields, "argument_keys": list(keys)}


def _desktop_approval_resolved(data: dict[str, Any]) -> dict[str, Any] | None:
    fields = {
        name: _bounded_text(data.get(name), 128 if name == "resolved_at" else 256)
        for name in ("approval_id", "call_id", "tool_name", "resolved_at")
    }
    decision = data.get("decision")
    outcome = data.get("outcome")
    if (
        any(value is None for value in fields.values())
        or decision not in {None, "allow_once", "deny"}
        or outcome not in {"applied", "denied", "expired", "cancelled", "interrupted"}
    ):
        return None
    return {**fields, "decision": decision, "outcome": outcome}


def _valid_argument_keys(value: Any) -> bool:
    if not isinstance(value, list) or len(value) > 64:
        return False
    if any(
        not isinstance(item, str) or len(item.encode("utf-8")) > 256 for item in value
    ):
        return False
    if value != sorted(set(value)):
        return False
    return (
        len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        <= 8 * 1024
    )


def _bounded_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > limit:
        return None
    return value


def _optional_bounded_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, limit)


def _emit_result_error(
    status: HTTPStatus,
    *,
    code: str,
    message: str,
    retryable: bool,
    write_sse_event: Callable[..., None],
    max_event_bytes: int | None,
    retry_after_ms: int | None = None,
    details: dict[str, Any] | None = None,
) -> tuple[HTTPStatus, dict[str, Any]]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if retry_after_ms is not None:
        error["retry_after_ms"] = retry_after_ms
    if details is not None:
        error["details"] = details
    payload = {"ok": False, "error": error}
    _safe_client_stream_event(
        event="error",
        data=error,
        write_sse_event=write_sse_event,
        max_event_bytes=max_event_bytes,
    )
    return status, payload


def _collect_stream_result(
    *,
    submission: TurnSubmission,
    run_id_for_meta: str | None,
    client_disconnected: bool,
    write_sse_event: Callable[..., None],
    max_event_bytes: int | None = None,
    cancel_on_timeout: bool = False,
) -> tuple[HTTPStatus, dict[str, Any]]:
    try:
        turn_response = submission.handle.result(
            timeout_s=max(0.0, float(submission.timeout_s))
        )
    except TimeoutError as exc:
        if cancel_on_timeout:
            submission.handle.cancel()
        return _emit_result_error(
            HTTPStatus.GATEWAY_TIMEOUT,
            code="turn_timeout",
            message=str(exc),
            retryable=True,
            write_sse_event=write_sse_event,
            max_event_bytes=max_event_bytes,
        )
    except RuntimeError as exc:
        if getattr(exc, "code", "") == "SESSION_TURN_BUSY":
            retry_after_ms = max(1000, int(getattr(exc, "retry_after_s", 1)) * 1000)
            return _emit_result_error(
                HTTPStatus.CONFLICT,
                code="SESSION_TURN_BUSY",
                message=str(exc),
                retryable=True,
                retry_after_ms=retry_after_ms,
                details={"retry_after_s": retry_after_ms // 1000},
                write_sse_event=write_sse_event,
                max_event_bytes=max_event_bytes,
            )
        return _emit_result_error(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="turn_failed",
            message=str(exc),
            retryable=False,
            write_sse_event=write_sse_event,
            max_event_bytes=max_event_bytes,
        )
    except Exception as exc:  # noqa: BLE001
        return _emit_result_error(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="turn_failed",
            message=str(exc),
            retryable=False,
            write_sse_event=write_sse_event,
            max_event_bytes=max_event_bytes,
        )
    if not client_disconnected:
        response_payload = turn_response_to_dict(turn_response)
        response_payload.setdefault("final_text", "")
        _safe_client_stream_event(
            event="response",
            data={"trace_id": run_id_for_meta, **response_payload},
            write_sse_event=write_sse_event,
            max_event_bytes=max_event_bytes,
        )
    return HTTPStatus.OK, {"ok": True}


def _serve_stream_submission(
    submission: TurnSubmission,
    *,
    request_id: str,
    desktop_client: bool,
    start_sse_response: Callable[[], None],
    write_sse_event: Callable[..., None],
) -> tuple[HTTPStatus, dict[str, Any], str | None, str | None]:
    session_id = submission.session_id
    run_id = submission.run_id
    max_event_bytes = _DESKTOP_SSE_EVENT_LIMIT if desktop_client else None
    client_disconnected = False
    try:
        start_sse_response()
        try:
            client_disconnected = not _safe_client_stream_event(
                event="meta",
                data={
                    "request_id": request_id,
                    "trace_id": run_id,
                    "session_id": session_id,
                },
                write_sse_event=write_sse_event,
                max_event_bytes=max_event_bytes,
            )
            if not client_disconnected:
                client_disconnected = not _emit_client_stream_chunks(
                    submission=submission,
                    run_id_for_meta=run_id,
                    write_sse_event=write_sse_event,
                    max_event_bytes=max_event_bytes,
                    desktop_client=desktop_client,
                )
            status, payload = _collect_stream_result(
                submission=submission,
                run_id_for_meta=run_id,
                client_disconnected=client_disconnected,
                write_sse_event=write_sse_event,
                max_event_bytes=max_event_bytes,
                cancel_on_timeout=desktop_client,
            )
            _safe_client_stream_event(
                event="done",
                data={"status": "complete" if status == HTTPStatus.OK else "error"},
                write_sse_event=write_sse_event,
                max_event_bytes=max_event_bytes,
            )
        except _StreamLimitExceeded:
            submission.handle.cancel()
            status, payload = _client_stream_error_payload(
                HTTPStatus.BAD_GATEWAY,
                code="stream_limit_exceeded",
                message="Desktop stream event exceeded its size limit.",
                retryable=False,
            )
            if _safe_client_stream_event(
                event="error",
                data=payload["error"],
                write_sse_event=write_sse_event,
                max_event_bytes=max_event_bytes,
            ):
                _safe_client_stream_event(
                    event="done",
                    data={"status": "error"},
                    write_sse_event=write_sse_event,
                    max_event_bytes=max_event_bytes,
                )
    finally:
        close_submission(submission)
    return status, payload, session_id, run_id


def _resolve_desktop_attachment_refs(
    body: dict[str, Any],
    *,
    desktop_client: bool,
    client_media: "ClientMediaCoordinator | None",
    client_identity: "ClientIdentity | None",
) -> tuple[tuple[str, ...], tuple[HTTPStatus, dict[str, Any]] | None]:
    attachment_ids = tuple(body.get("attachments", ()))
    if not desktop_client or not attachment_ids:
        return (), None
    if client_media is None or client_identity is None:
        return (), _client_stream_error_payload(
            HTTPStatus.FORBIDDEN,
            code="forbidden",
            message="Request is not authorized.",
            retryable=False,
        )
    from openminion.api.server.client_media import ClientMediaError

    try:
        return (
            client_media.resolve_for_turn(
                client_identity,
                str(body["session_id"]),
                str(body["trace_id"]),
                attachment_ids,
            ),
            None,
        )
    except ClientMediaError as exc:
        return (), _client_stream_error_payload(
            exc.status,
            code=exc.code,
            message=str(exc),
            retryable=exc.retryable,
            retry_after_ms=exc.retry_after_ms,
        )


def _prepare_stream_submission(
    *,
    body: dict[str, Any],
    config_path: str | None,
    runtime: APIRuntime | None,
    desktop_client: bool,
    desktop_approval_requester: "DesktopApprovalRequester | None",
    client_media: "ClientMediaCoordinator | None",
    client_identity: "ClientIdentity | None",
    resolved_request_id: str,
    started_at: float,
    logger: logging.Logger,
    observe_request_metrics: Callable[..., int],
    log_request_done: Callable[..., None],
    write_json: Callable[..., None],
) -> TurnSubmission | None:
    error: tuple[HTTPStatus, dict[str, Any]] | None = None
    if desktop_client and (message := _validate_desktop_turn_body(body)):
        error = _client_stream_error_payload(
            HTTPStatus.BAD_REQUEST,
            code="invalid_request",
            message=message,
            retryable=False,
        )
    submission_body = dict(body)
    submission_body.pop("attachments", None)
    resolved_refs: tuple[str, ...] = ()
    if error is None:
        resolved_refs, error = _resolve_desktop_attachment_refs(
            body,
            desktop_client=desktop_client,
            client_media=client_media,
            client_identity=client_identity,
        )
    submission: TurnSubmission | None = None
    if error is None:
        try:
            submission, error_status, error_payload = _open_client_stream_submission(
                body=submission_body,
                config_path=config_path,
                runtime=runtime,
                desktop_approval_requester=desktop_approval_requester,
                resolved_attachment_refs=resolved_refs,
            )
            _bind_desktop_media_completion(
                submission,
                body=body,
                resolved_refs=resolved_refs,
                client_media=client_media,
                client_identity=client_identity,
            )
        except Exception:
            _abort_pre_sse_submission(
                submission,
                body=body,
                resolved_refs=resolved_refs,
                client_media=client_media,
                client_identity=client_identity,
            )
            raise
        if submission is None:
            assert error_status is not None and error_payload is not None
            error = error_status, error_payload
            _unbind_desktop_media(
                body,
                resolved_refs=resolved_refs,
                client_media=client_media,
                client_identity=client_identity,
            )
    if error is not None:
        _record_client_stream_response(
            status=error[0],
            payload=error[1],
            resolved_request_id=resolved_request_id,
            session_id_for_meta=None,
            run_id_for_meta=None,
            started_at=started_at,
            logger=logger,
            observe_request_metrics=observe_request_metrics,
            log_request_done=log_request_done,
            write_json=write_json,
        )
        return None
    assert submission is not None
    return submission


def _bind_desktop_media_completion(
    submission: TurnSubmission | None,
    *,
    body: dict[str, Any],
    resolved_refs: tuple[str, ...],
    client_media: "ClientMediaCoordinator | None",
    client_identity: "ClientIdentity | None",
) -> None:
    if submission is None or not resolved_refs or client_media is None:
        return
    if client_identity is None:
        return
    session_id, trace_id = str(body["session_id"]), str(body["trace_id"])
    submission.handle.add_done_callback(
        lambda: client_media.complete_trace(client_identity, session_id, trace_id)
    )


def _abort_pre_sse_submission(
    submission: TurnSubmission | None,
    *,
    body: dict[str, Any],
    resolved_refs: tuple[str, ...],
    client_media: "ClientMediaCoordinator | None",
    client_identity: "ClientIdentity | None",
) -> None:
    if submission is None:
        _unbind_desktop_media(
            body,
            resolved_refs=resolved_refs,
            client_media=client_media,
            client_identity=client_identity,
        )
        return
    terminal = False
    try:
        submission.handle.cancel()
        submission.handle.result(timeout_s=submission.timeout_s)
        terminal = True
    except (RuntimeError, TimeoutError):
        pass
    finally:
        close_submission(submission)
    if terminal:
        _complete_desktop_media(
            body,
            client_media=client_media,
            client_identity=client_identity,
        )


def _complete_desktop_media(
    body: dict[str, Any],
    *,
    client_media: "ClientMediaCoordinator | None",
    client_identity: "ClientIdentity | None",
) -> None:
    if client_media is None or client_identity is None:
        return
    client_media.complete_trace(
        client_identity,
        str(body["session_id"]),
        str(body["trace_id"]),
    )


def _unbind_desktop_media(
    body: dict[str, Any],
    *,
    resolved_refs: tuple[str, ...],
    client_media: "ClientMediaCoordinator | None",
    client_identity: "ClientIdentity | None",
) -> None:
    if not resolved_refs or client_media is None or client_identity is None:
        return
    client_media.unbind_before_start(
        client_identity,
        str(body["session_id"]),
        str(body["trace_id"]),
    )


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
    desktop_client: bool = False,
    desktop_approval_requester: "DesktopApprovalRequester | None" = None,
    client_media: "ClientMediaCoordinator | None" = None,
    client_identity: "ClientIdentity | None" = None,
) -> None:
    resolved_request_id = normalize_request_id(request_id)
    started_at = perf_counter()
    logger = logging.getLogger("openminion.api")
    submission = _prepare_stream_submission(
        body=body,
        config_path=config_path,
        runtime=runtime,
        desktop_client=desktop_client,
        desktop_approval_requester=desktop_approval_requester,
        client_media=client_media,
        client_identity=client_identity,
        resolved_request_id=resolved_request_id,
        started_at=started_at,
        logger=logger,
        observe_request_metrics=observe_request_metrics,
        log_request_done=log_request_done,
        write_json=write_json,
    )
    if submission is None:
        return

    (
        status_for_metrics,
        payload_for_metrics,
        session_id_for_meta,
        run_id_for_meta,
    ) = _serve_stream_submission(
        submission,
        request_id=resolved_request_id,
        desktop_client=desktop_client,
        start_sse_response=start_sse_response,
        write_sse_event=write_sse_event,
    )

    _record_client_stream_response(
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


def _validate_desktop_turn_body(body: dict[str, Any]) -> str | None:
    if not _DESKTOP_TURN_REQUIRED_KEYS <= set(body) or set(body) - _DESKTOP_TURN_KEYS:
        return "Desktop turn fields do not match the protocol 1 schema."
    for key in ("trace_id", "session_id", "agent_id", "input_text", "idempotency_key"):
        value = body.get(key)
        if not isinstance(value, str) or not value.strip():
            return f"{key} must be a non-empty string."
    if body.get("mode") != "oneshot" or body.get("stream") is not True:
        return "Desktop turns require oneshot streaming mode."
    if body.get("channel") != "console" or body.get("user") != "api-user":
        return "Desktop turn channel identity is invalid."
    attachments = body.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > 4:
        return "attachments must contain at most four distinct media IDs."
    if any(
        not isinstance(media_id, str) or _MEDIA_ID.fullmatch(media_id) is None
        for media_id in attachments
    ) or len(set(attachments)) != len(attachments):
        return "attachments must contain at most four distinct media IDs."
    for key in ("trace_id", "idempotency_key"):
        try:
            UUID(str(body[key]))
        except ValueError:
            return f"{key} must be a UUID."
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > 256 * 1024:
        return "Desktop turn body exceeds its size limit."
    return None


__all__ = ["handle_turn_stream_request"]
