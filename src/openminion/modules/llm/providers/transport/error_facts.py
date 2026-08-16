"""Bounded facts from OpenAI-compatible HTTP error responses."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from openminion.base.redaction import redact_sensitive_text
from openminion.modules.llm.constants import (
    UPSTREAM_ERROR_FIELD_MAX_CHARS,
    UPSTREAM_ERROR_MESSAGE_MAX_CHARS,
)


def openai_error_facts(
    raw_body: str,
    *,
    status_code: int,
    request_id: str = "",
) -> dict[str, Any]:
    payload = _json_object(raw_body)
    raw_error = payload.get("error")
    if isinstance(raw_error, Mapping):
        error = raw_error
    elif isinstance(raw_error, str):
        error = {"message": raw_error}
    else:
        error = payload
    facts: dict[str, Any] = {"status_code": status_code}
    for source, target, limit in (
        ("code", "upstream_code", UPSTREAM_ERROR_FIELD_MAX_CHARS),
        ("message", "upstream_message", UPSTREAM_ERROR_MESSAGE_MAX_CHARS),
        ("type", "upstream_type", UPSTREAM_ERROR_FIELD_MAX_CHARS),
        ("param", "upstream_param", UPSTREAM_ERROR_FIELD_MAX_CHARS),
    ):
        value = _bounded(error.get(source), limit)
        if value:
            facts[target] = value
    response_request_id = request_id or _bounded(
        payload.get("request_id") or payload.get("requestId"),
        UPSTREAM_ERROR_FIELD_MAX_CHARS,
    )
    if response_request_id:
        facts["request_id"] = response_request_id
    return facts


def openai_error_message(facts: Mapping[str, Any], *, status_code: int) -> str:
    return str(facts.get("upstream_message") or f"HTTP {status_code}")


def malformed_response_facts(
    raw_body: str, *, status_code: int, error: str
) -> dict[str, Any]:
    return {
        "status_code": status_code,
        "response_bytes": len(raw_body.encode("utf-8")),
        "error": error,
    }


def _json_object(raw_body: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, TypeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _bounded(value: Any, limit: int) -> str:
    redacted, _ = redact_sensitive_text(str(value or "").strip())
    return redacted[:limit]


__all__ = [
    "malformed_response_facts",
    "openai_error_facts",
    "openai_error_message",
]
