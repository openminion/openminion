import json
from pathlib import Path
import time
from typing import Any
from collections.abc import Mapping

from openminion.base.config.env import EnvironmentConfig
from openminion.modules.telemetry.constants import TRACE_HOME_ROOT_METADATA_KEY
from openminion.modules.telemetry.trace.structured import (
    trace_requests_enabled as _trace_requests_enabled,
)
from openminion.modules.telemetry.trace.layout import (
    build_trace_file_path,
    resolve_trace_root,
)


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in dict(headers or {}).items():
        lowered = str(key).strip().lower()
        if lowered in {"authorization", "x-api-key", "api-key"}:
            redacted[str(key)] = "<redacted>"
        else:
            redacted[str(key)] = str(value)
    return redacted


def _resolve_trace_context(
    trace_metadata: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    meta = dict(trace_metadata or {})
    try:
        inference_step = int(str(meta.get("inference_step") or "0").strip() or 0)
    except ValueError:
        inference_step = 0
    trace = {
        "session_id": str(meta.get("session_id") or "").strip(),
        "turn_id": str(meta.get("turn_id") or "").strip(),
        "inference_step": inference_step,
        "label": str(meta.get("trace_label") or meta.get("label") or "call").strip(),
        "trace_id": str(meta.get("trace_id") or ""),
        "agent_id": str(meta.get("agent_id") or ""),
        "run_id": str(meta.get("run_id") or ""),
    }
    return meta, trace


def _resolve_trace_path(
    meta: Mapping[str, Any],
    *,
    session_id: str,
    turn_id: str,
    inference_step: int,
    label: str,
    trace_id: str,
    suffix: str,
) -> Path | None:
    trace_root = resolve_trace_root(home_root=_resolve_home_root(meta))
    trace_path, _ = build_trace_file_path(
        trace_root,
        session_id=session_id,
        turn_id=turn_id,
        inference_step=inference_step,
        label=label,
        suffix=suffix,
    )
    try:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    if trace_path.exists():
        nonce = trace_id or str(time.time_ns())
        trace_path = trace_path.with_name(f"{trace_path.stem}-{nonce}.json")
    return trace_path


def trace_http_json_request(
    *,
    trace_metadata: dict[str, Any] | None,
    provider_name: str,
    url: str,
    body_json: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    timeout_seconds: int,
    transport: str,
    method: str = "POST",
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> None:
    """Write the exact JSON body sent to the provider (headers redacted).

    Output path: `<trace_root>/llm/<agent>/<run>/<step-label>-http.json` when
    `OPENMINION_TRACE_REQUESTS=1` is enabled.
    """
    if not _trace_requests_enabled(env=env):
        return
    meta, trace = _resolve_trace_context(trace_metadata)
    trace_path = _resolve_trace_path(
        meta,
        session_id=str(trace["session_id"]),
        turn_id=str(trace["turn_id"]),
        inference_step=int(trace["inference_step"]),
        label=str(trace["label"]),
        trace_id=str(trace["trace_id"]),
        suffix="-http.json",
    )
    if trace_path is None:
        return

    parsed_json: Any = payload
    if parsed_json is None:
        try:
            parsed_json = json.loads(body_json)
        except json.JSONDecodeError:
            parsed_json = None

    payload_out = {
        "event": "http_request",
        "provider": str(provider_name),
        "transport": str(transport),
        "url": str(url),
        "method": str(method or "POST").upper(),
        "timeout_seconds": int(timeout_seconds),
        "headers": _redact_headers(headers),
        # Exact serialized JSON request body sent on the wire.
        "json_body": body_json,
        # Parsed form of the serialized body for easier inspection.
        "json": parsed_json,
        "trace": trace,
    }
    try:
        trace_path.write_text(
            json.dumps(payload_out, indent=2, sort_keys=True), encoding="utf-8"
        )
    except Exception:
        return


def trace_http_json_response(
    *,
    trace_metadata: dict[str, Any] | None,
    provider_name: str,
    url: str,
    status_code: int,
    body_text: str,
    transport: str,
    parsed_json: Any | None = None,
    parse_error: str = "",
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> None:
    """Write the exact unary HTTP response body received from the provider."""
    if not _trace_requests_enabled(env=env):
        return
    meta, trace = _resolve_trace_context(trace_metadata)
    trace_path = _resolve_trace_path(
        meta,
        session_id=str(trace["session_id"]),
        turn_id=str(trace["turn_id"]),
        inference_step=int(trace["inference_step"]),
        label=str(trace["label"]),
        trace_id=str(trace["trace_id"]),
        suffix="-http-response.json",
    )
    if trace_path is None:
        return

    payload_out = {
        "event": "http_response",
        "provider": str(provider_name),
        "transport": str(transport),
        "url": str(url),
        "status_code": int(status_code),
        "body_text": str(body_text or ""),
        "json": parsed_json,
        "json_parse_error": str(parse_error or ""),
        "lane": {
            "provider": str(provider_name),
            "transport": str(transport),
            "status_code": int(status_code),
            "url": str(url),
        },
        "trace": trace,
    }
    try:
        trace_path.write_text(
            json.dumps(payload_out, indent=2, sort_keys=True), encoding="utf-8"
        )
    except Exception:
        return


def _resolve_home_root(metadata: Mapping[str, Any]) -> Path | None:
    raw_value = str(metadata.get(TRACE_HOME_ROOT_METADATA_KEY) or "").strip()
    if not raw_value:
        return None
    return Path(raw_value)
