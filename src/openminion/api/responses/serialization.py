"""Response-shaping helpers for the developer API."""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import Any, Mapping, Optional
from uuid import uuid4

from openminion.base.errors.adapt import (
    error_info_from_exception,
    error_info_from_mapping,
)
from openminion.base.errors.contracts import ErrorInfo

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def error_response(
    status: HTTPStatus,
    *,
    code: Optional[str] = None,
    message: Optional[str] = None,
    details: Optional[dict] = None,
    retryable: bool = False,
    retry_after_ms: Optional[int] = None,
    error: ErrorInfo | BaseException | Mapping[str, Any] | None = None,
) -> tuple[HTTPStatus, dict]:
    resolved = _resolve_error_info(
        error=error,
        code=code,
        message=message,
        details=details,
    )
    return (
        status,
        {
            "ok": False,
            "error": {
                "code": resolved.code,
                "message": resolved.message,
                "details": dict(resolved.details),
                "retryable": bool(retryable),
                "retry_after_ms": retry_after_ms,
            },
        },
    )


def attach_response_meta(
    payload: dict,
    *,
    request_id: str,
    method: str,
    path: str,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict:
    response_payload = dict(payload)
    existing_meta = response_payload.get("meta")
    if isinstance(existing_meta, dict):
        meta = dict(existing_meta)
    else:
        meta = {}
    meta["request_id"] = request_id
    meta["method"] = method
    meta["path"] = path
    if session_id:
        meta["session_id"] = session_id
    if run_id:
        meta["run_id"] = run_id
    response_payload["meta"] = meta
    return response_payload


def normalize_request_id(raw_request_id: Optional[str]) -> str:
    candidate = (raw_request_id or "").strip()
    if candidate and _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid4().hex


def response_error_code(payload: Optional[dict]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    if not isinstance(code, str):
        return None
    normalized = code.strip()
    return normalized or None


def _resolve_error_info(
    *,
    error: ErrorInfo | BaseException | Mapping[str, Any] | None,
    code: Optional[str],
    message: Optional[str],
    details: Optional[dict],
) -> ErrorInfo:
    if isinstance(error, ErrorInfo):
        return error
    if isinstance(error, BaseException):
        return error_info_from_exception(error)
    if isinstance(error, Mapping):
        return error_info_from_mapping(error)
    return error_info_from_mapping(
        {
            "code": code,
            "message": message,
            "details": details,
        }
    )
