"""Typed request/response/event protocol frames."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from openminion.base.errors.adapt import (
    error_info_from_exception,
    error_info_from_mapping,
)
from openminion.base.errors.contracts import ErrorInfo


@dataclass(frozen=True)
class ErrorPayload:
    code: str
    message: str
    details: dict[str, Any] | None = None
    retryable: bool | None = None
    retry_after_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details is not None:
            payload["details"] = self.details
        if self.retryable is not None:
            payload["retryable"] = self.retryable
        if self.retry_after_ms is not None:
            payload["retry_after_ms"] = self.retry_after_ms
        return payload


class ProtocolError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms

    def to_payload(self) -> ErrorPayload:
        return _error_payload_from_any(self)


@dataclass(frozen=True)
class RequestFrame:
    id: str
    method: str
    params: dict[str, Any]
    type: str = "req"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }


@dataclass(frozen=True)
class ResponseFrame:
    id: str
    ok: bool
    payload: dict[str, Any]
    error: ErrorPayload | None = None
    type: str = "res"

    def to_dict(self) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "type": self.type,
            "id": self.id,
            "ok": self.ok,
            "payload": self.payload,
        }
        if self.error is not None:
            frame["error"] = self.error.to_dict()
        return frame


@dataclass(frozen=True)
class EventFrame:
    event: str
    payload: dict[str, Any]
    seq: int | None = None
    type: str = "event"

    def to_dict(self) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "type": self.type,
            "event": self.event,
            "payload": self.payload,
        }
        if self.seq is not None:
            frame["seq"] = self.seq
        return frame


@dataclass(frozen=True)
class ConnectParams:
    min_protocol: int
    max_protocol: int
    client: dict[str, Any]


Frame = RequestFrame | ResponseFrame | EventFrame


def parse_frame(raw: Mapping[str, Any]) -> Frame:
    frame_type = raw.get("type")
    if frame_type == "req":
        return parse_request_frame(raw)
    if frame_type == "res":
        return parse_response_frame(raw)
    if frame_type == "event":
        return parse_event_frame(raw)
    raise ProtocolError(
        "invalid_frame_type",
        "Frame type must be one of req|res|event",
        details={"type": frame_type},
    )


def parse_request_frame(raw: Mapping[str, Any]) -> RequestFrame:
    request_id = _parse_non_empty_string(raw.get("id"), field_name="id")
    method = _parse_non_empty_string(raw.get("method"), field_name="method")

    params_raw = raw.get("params", {})
    if not isinstance(params_raw, dict):
        raise ProtocolError(
            "invalid_params_type",
            "Request params must be an object",
            details={"field": "params"},
        )

    return RequestFrame(id=request_id, method=method, params=dict(params_raw))


def parse_response_frame(raw: Mapping[str, Any]) -> ResponseFrame:
    request_id = _parse_non_empty_string(raw.get("id"), field_name="id")
    ok_raw = raw.get("ok")
    if not isinstance(ok_raw, bool):
        raise ProtocolError(
            "invalid_response_ok",
            "Response ok must be a boolean",
            details={"field": "ok"},
        )
    payload_raw = raw.get("payload", {})
    if not isinstance(payload_raw, dict):
        raise ProtocolError(
            "invalid_response_payload",
            "Response payload must be an object",
            details={"field": "payload"},
        )

    error_raw = raw.get("error")
    error_payload: ErrorPayload | None = None
    if error_raw is not None:
        if not isinstance(error_raw, dict):
            raise ProtocolError(
                "invalid_error_payload",
                "Response error must be an object",
                details={"field": "error"},
            )
        code = _parse_non_empty_string(error_raw.get("code"), field_name="error.code")
        message = _parse_non_empty_string(
            error_raw.get("message"), field_name="error.message"
        )
        details = error_raw.get("details")
        retryable = error_raw.get("retryable")
        retry_after_ms = error_raw.get("retry_after_ms")
        if retryable is not None and not isinstance(retryable, bool):
            raise ProtocolError(
                "invalid_error_retryable",
                "error.retryable must be a boolean",
                details={"field": "error.retryable"},
            )
        if retry_after_ms is not None and not isinstance(retry_after_ms, int):
            raise ProtocolError(
                "invalid_error_retry_after",
                "error.retry_after_ms must be an integer",
                details={"field": "error.retry_after_ms"},
            )
        if details is not None and not isinstance(details, dict):
            raise ProtocolError(
                "invalid_error_details",
                "error.details must be an object",
                details={"field": "error.details"},
            )
        error_payload = ErrorPayload(
            code=code,
            message=message,
            details=details,
            retryable=retryable,
            retry_after_ms=retry_after_ms,
        )

    return ResponseFrame(
        id=request_id,
        ok=ok_raw,
        payload=dict(payload_raw),
        error=error_payload,
    )


def parse_event_frame(raw: Mapping[str, Any]) -> EventFrame:
    event = _parse_non_empty_string(raw.get("event"), field_name="event")
    payload_raw = raw.get("payload", {})
    if not isinstance(payload_raw, dict):
        raise ProtocolError(
            "invalid_event_payload",
            "Event payload must be an object",
            details={"field": "payload"},
        )
    seq_raw = raw.get("seq")
    if seq_raw is not None and (not isinstance(seq_raw, int) or seq_raw < 0):
        raise ProtocolError(
            "invalid_event_seq",
            "Event seq must be a non-negative integer",
            details={"field": "seq"},
        )
    return EventFrame(event=event, payload=dict(payload_raw), seq=seq_raw)


def parse_connect_params(raw: Mapping[str, Any]) -> ConnectParams:
    min_protocol = raw.get("min_protocol")
    max_protocol = raw.get("max_protocol")
    if not isinstance(min_protocol, int) or not isinstance(max_protocol, int):
        raise ProtocolError(
            "invalid_connect_range",
            "connect params require integer min_protocol and max_protocol",
            details={
                "params": {"min_protocol": min_protocol, "max_protocol": max_protocol}
            },
        )
    client_raw = raw.get("client", {})
    if not isinstance(client_raw, dict):
        raise ProtocolError(
            "invalid_connect_client",
            "connect params client must be an object",
            details={"field": "client"},
        )
    if min_protocol <= 0 or max_protocol <= 0:
        raise ProtocolError(
            "invalid_connect_range",
            "connect protocol values must be positive integers",
            details={
                "params": {"min_protocol": min_protocol, "max_protocol": max_protocol}
            },
        )
    if min_protocol > max_protocol:
        raise ProtocolError(
            "invalid_connect_range",
            "connect min_protocol cannot be greater than max_protocol",
            details={
                "params": {"min_protocol": min_protocol, "max_protocol": max_protocol}
            },
        )
    return ConnectParams(
        min_protocol=min_protocol,
        max_protocol=max_protocol,
        client=dict(client_raw),
    )


def negotiate_protocol(
    *,
    client_min: int,
    client_max: int,
    server_min: int,
    server_max: int,
) -> int:
    lower_bound = max(client_min, server_min)
    upper_bound = min(client_max, server_max)
    if lower_bound > upper_bound:
        raise ProtocolError(
            "protocol_mismatch",
            "No compatible protocol version between client and server",
            details={
                "client": {"min_protocol": client_min, "max_protocol": client_max},
                "server": {"min_protocol": server_min, "max_protocol": server_max},
            },
            retryable=False,
        )
    return upper_bound


def build_error_response(
    request_id: str,
    error: ProtocolError | ErrorInfo | Mapping[str, Any] | BaseException,
) -> ResponseFrame:
    return ResponseFrame(
        id=request_id,
        ok=False,
        payload={},
        error=_error_payload_from_any(error),
    )


def build_success_response(
    request_id: str, payload: dict[str, Any] | None = None
) -> ResponseFrame:
    return ResponseFrame(id=request_id, ok=True, payload=dict(payload or {}))


def _parse_non_empty_string(raw: Any, *, field_name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ProtocolError(
            "invalid_frame_field",
            f"Frame field '{field_name}' must be a non-empty string",
            details={"field": field_name},
        )
    return raw.strip()


def _error_payload_from_any(
    error: ProtocolError | ErrorInfo | Mapping[str, Any] | BaseException,
) -> ErrorPayload:
    retryable: bool | None = None
    retry_after_ms: int | None = None
    if isinstance(error, ProtocolError):
        retryable = error.retryable
        retry_after_ms = error.retry_after_ms
        info = error_info_from_exception(error)
    elif isinstance(error, ErrorInfo):
        info = error
    elif isinstance(error, Mapping):
        info = error_info_from_mapping(error)
        raw_retryable = error.get("retryable")
        raw_retry_after_ms = error.get("retry_after_ms")
        retryable = raw_retryable if isinstance(raw_retryable, bool) else None
        retry_after_ms = (
            raw_retry_after_ms if isinstance(raw_retry_after_ms, int) else None
        )
    else:
        info = error_info_from_exception(error)
        raw_retryable = getattr(error, "retryable", None)
        raw_retry_after_ms = getattr(error, "retry_after_ms", None)
        retryable = raw_retryable if isinstance(raw_retryable, bool) else None
        retry_after_ms = (
            raw_retry_after_ms if isinstance(raw_retry_after_ms, int) else None
        )
    return ErrorPayload(
        code=info.code,
        message=info.message,
        details=dict(info.details) or None,
        retryable=retryable,
        retry_after_ms=retry_after_ms,
    )
