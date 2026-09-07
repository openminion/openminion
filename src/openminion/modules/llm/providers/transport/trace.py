import json
from pathlib import Path
import time
from typing import Any
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from openminion.base.config.env import EnvironmentConfig
from openminion.modules.telemetry.constants import TRACE_HOME_ROOT_METADATA_KEY
from openminion.modules.telemetry.trace.structured import (
    trace_requests_enabled as _trace_requests_enabled,
)
from openminion.modules.telemetry.trace.layout import (
    build_trace_file_path,
    resolve_trace_root,
    write_protected_trace_file,
)


_CREDENTIAL_HEADERS = {
    "api-key",
    "authorization",
    "cf-access-client-secret",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-access-token",
    "x-amz-security-token",
    "x-api-key",
    "x-auth-token",
    "x-goog-api-key",
}
_CREDENTIAL_QUERY_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
    "x-amz-credential",
    "x-amz-signature",
    "x-amz-security-token",
}


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.strip().lower()
        if lowered in _CREDENTIAL_HEADERS:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def _redact_url(url: str) -> str:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    if parsed.username is not None:
        password = ":<redacted>" if parsed.password is not None else ""
        host = f"<redacted>{password}@{host}"
    query = urlencode(
        [
            (
                key,
                "<redacted>"
                if key.strip().lower() in _CREDENTIAL_QUERY_NAMES
                else value,
            )
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ],
        doseq=True,
        safe="<>",
    )
    return urlunsplit((parsed.scheme, host, parsed.path, query, ""))


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
    trace: Mapping[str, Any],
    *,
    suffix: str,
) -> Path | None:
    trace_root = resolve_trace_root(home_root=_resolve_home_root(meta))
    trace_path, _ = build_trace_file_path(
        trace_root,
        session_id=trace["session_id"],
        turn_id=trace["turn_id"],
        inference_step=trace["inference_step"],
        label=trace["label"],
        suffix=suffix,
    )
    try:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    if trace_path.exists():
        nonce = trace["trace_id"] or str(time.time_ns())
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
    trace_path = _resolve_trace_path(meta, trace, suffix="-http.json")
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
        "provider": provider_name,
        "transport": transport,
        "url": _redact_url(url),
        "method": (method or "POST").upper(),
        "timeout_seconds": timeout_seconds,
        "headers": _redact_headers(headers),
        # Exact serialized JSON request body sent on the wire.
        "json_body": body_json,
        # Parsed form of the serialized body for easier inspection.
        "json": parsed_json,
        "trace": trace,
    }
    try:
        write_protected_trace_file(
            trace_path,
            json.dumps(payload_out, indent=2, sort_keys=True),
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
    trace_path = _resolve_trace_path(meta, trace, suffix="-http-response.json")
    if trace_path is None:
        return

    payload_out = {
        "event": "http_response",
        "provider": provider_name,
        "transport": transport,
        "url": _redact_url(url),
        "status_code": status_code,
        "body_text": body_text,
        "json": parsed_json,
        "json_parse_error": parse_error,
        "lane": {
            "provider": provider_name,
            "transport": transport,
            "status_code": status_code,
            "url": _redact_url(url),
        },
        "trace": trace,
    }
    try:
        write_protected_trace_file(
            trace_path,
            json.dumps(payload_out, indent=2, sort_keys=True),
        )
    except Exception:
        return


def trace_http_sse_response(
    *,
    trace_metadata: dict[str, Any] | None,
    provider_name: str,
    url: str,
    status_code: int,
    request_id: str,
    lines: list[str],
    complete: bool,
    transport: str,
    error: Mapping[str, Any] | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> None:
    """Write the decoded SSE lines consumed by the provider parser."""
    if not _trace_requests_enabled(env=env):
        return
    meta, trace = _resolve_trace_context(trace_metadata)
    trace_path = _resolve_trace_path(
        meta,
        trace,
        suffix="-http-sse-response.json",
    )
    if trace_path is None:
        return

    payload_out = {
        "event": "http_sse_response",
        "provider": provider_name,
        "transport": transport,
        "url": _redact_url(url),
        "status_code": status_code,
        "request_id": request_id,
        "lines": list(lines),
        "complete": complete,
        "error": dict(error or {}),
        "trace": trace,
    }
    try:
        write_protected_trace_file(
            trace_path,
            json.dumps(payload_out, indent=2, sort_keys=True),
        )
    except (OSError, TypeError, ValueError):
        return


def _resolve_home_root(metadata: Mapping[str, Any]) -> Path | None:
    raw_value = str(metadata.get(TRACE_HOME_ROOT_METADATA_KEY) or "").strip()
    if not raw_value:
        return None
    return Path(raw_value)
