from __future__ import annotations

import json
from typing import Optional


def parse_json_request_body(*, content_length_raw: str, raw_body: str) -> dict:
    try:
        content_length = int(content_length_raw)
    except ValueError as exc:
        raise ValueError("Invalid Content-Length header.") from exc

    if content_length <= 0:
        raise ValueError("JSON request body is required.")

    if len(raw_body.encode("utf-8")) < content_length:
        raise ValueError("Request body is shorter than Content-Length.")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be valid JSON.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")

    return payload


def parse_bool_query_value(raw_value: Optional[str]) -> bool:
    if raw_value is None:
        return False
    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("`reset` must be one of: true/false/1/0/yes/no/on/off.")


def parse_positive_int_query_value(
    *,
    raw_value: Optional[str],
    default_value: int,
    field_name: str,
) -> int:
    if raw_value is None:
        return int(default_value)
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"`{field_name}` must be an integer.") from exc
    if parsed <= 0:
        raise ValueError(f"`{field_name}` must be greater than zero.")
    return parsed


def v1_tool_arguments(body: dict) -> dict:
    raw_arguments = body.get("arguments")
    if raw_arguments is None:
        return {}
    if not isinstance(raw_arguments, dict):
        raise ValueError("`arguments` must be an object.")
    return dict(raw_arguments)


def v1_turn_timeout_seconds(body: dict, runtime) -> float:
    timeout_raw = body.get("timeout_seconds")
    if timeout_raw is None:
        return float(runtime.config.gateway.api_turn_timeout_seconds)
    try:
        timeout = float(timeout_raw)
    except (TypeError, ValueError):
        raise ValueError("`timeout_seconds` must be a number greater than zero.")
    if timeout <= 0:
        raise ValueError("`timeout_seconds` must be greater than zero.")
    return timeout
