from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from collections.abc import Callable
from typing import Any, Mapping

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config

from ...errors import LLMCtlError
from .debug import (
    llm_debug_max_chars,
    truncate_debug_value,
    write_llm_debug_event,
)
from .trace import trace_http_json_request, trace_http_json_response

_DebugWriter = Callable[[dict[str, Any]], None]


def curl_json_post(
    *,
    url: str,
    payload: dict[str, Any],
    body_json: str | None,
    headers: dict[str, str],
    timeout_seconds: int,
    provider_name: str,
    reason: str,
    with_default_user_agent_fn,
    trace_metadata: dict[str, Any] | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> dict[str, Any]:
    env_owner = resolve_environment_config(env=env)
    request_headers = with_default_user_agent_fn(headers)
    write_event = _curl_debug_writer(env_owner)
    trace_id = uuid.uuid4().hex
    max_chars = llm_debug_max_chars(env=env_owner)
    serialized_body = body_json if body_json is not None else json.dumps(payload)

    _write_curl_request_event(
        write_event=write_event,
        provider_name=provider_name,
        trace_id=trace_id,
        url=url,
        timeout_seconds=timeout_seconds,
        payload=payload,
        max_chars=max_chars,
    )
    _trace_curl_request(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        body_json=serialized_body,
        payload=payload,
        headers=request_headers,
        timeout_seconds=timeout_seconds,
        env=env_owner,
    )

    if shutil.which("curl") is None:
        raise LLMCtlError("PROVIDER_ERROR", f"{provider_name} request failed: {reason}")

    result = _run_curl_post(
        url=url,
        request_headers=request_headers,
        serialized_body=serialized_body,
        timeout_seconds=timeout_seconds,
    )
    _raise_curl_process_error(
        result=result,
        reason=reason,
        provider_name=provider_name,
        trace_id=trace_id,
        url=url,
        max_chars=max_chars,
        write_event=write_event,
    )

    raw_body, status_code = _split_curl_response(result.stdout)
    if status_code >= 400:
        _trace_and_write_error(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            raw_body=raw_body,
            trace_id=trace_id,
            max_chars=max_chars,
            env_owner=env_owner,
            write_event=write_event,
        )
        _raise_status_error(
            provider_name=provider_name,
            status_code=status_code,
            raw_body=raw_body,
        )

    parsed = _parse_curl_response(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        status_code=status_code,
        raw_body=raw_body,
        trace_id=trace_id,
        max_chars=max_chars,
        env_owner=env_owner,
        write_event=write_event,
    )
    _write_curl_response_event(
        write_event=write_event,
        provider_name=provider_name,
        trace_id=trace_id,
        url=url,
        parsed=parsed,
        max_chars=max_chars,
    )
    return parsed


def _curl_debug_writer(env_owner: EnvironmentConfig) -> _DebugWriter:
    def _write(event: dict[str, Any]) -> None:
        write_llm_debug_event(event, env=env_owner)

    return _write


def _write_curl_request_event(
    *,
    write_event: _DebugWriter,
    provider_name: str,
    trace_id: str,
    url: str,
    timeout_seconds: int,
    payload: dict[str, Any],
    max_chars: int,
) -> None:
    write_event(
        {
            "event": "request",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "timeout_seconds": timeout_seconds,
            "payload": truncate_debug_value(payload, max_chars),
            "transport": "curl",
        }
    )


def _trace_curl_request(
    *,
    trace_metadata: dict[str, Any] | None,
    provider_name: str,
    url: str,
    body_json: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: int,
    env: EnvironmentConfig,
) -> None:
    trace_http_json_request(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        body_json=body_json,
        payload=payload,
        headers=headers,
        timeout_seconds=timeout_seconds,
        transport="curl",
        env=env,
    )


def _run_curl_post(
    *,
    url: str,
    request_headers: dict[str, str],
    serialized_body: str,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _curl_args(
            url=url,
            request_headers=request_headers,
            serialized_body=serialized_body,
            timeout_seconds=timeout_seconds,
        ),
        text=True,
        capture_output=True,
    )


def _raise_curl_process_error(
    *,
    result: subprocess.CompletedProcess[str],
    reason: str,
    provider_name: str,
    trace_id: str,
    url: str,
    max_chars: int,
    write_event: _DebugWriter,
) -> None:
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout or "").strip() or reason
    write_event(
        {
            "event": "error",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "error": detail[:max_chars],
            "transport": "curl",
        }
    )
    raise LLMCtlError("PROVIDER_ERROR", f"{provider_name} request failed: {detail}")


def _write_curl_response_event(
    *,
    write_event: _DebugWriter,
    provider_name: str,
    trace_id: str,
    url: str,
    parsed: dict[str, Any],
    max_chars: int,
) -> None:
    write_event(
        {
            "event": "response",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "payload": truncate_debug_value(parsed, max_chars),
            "transport": "curl",
        }
    )


def _curl_args(
    *,
    url: str,
    request_headers: dict[str, str],
    serialized_body: str,
    timeout_seconds: int,
) -> list[str]:
    args = ["curl", "-sS", "-X", "POST", url, "--max-time", str(timeout_seconds)]
    for key, value in request_headers.items():
        args.extend(["-H", f"{key}: {value}"])
    args.extend(["-d", serialized_body, "-w", "\n%{http_code}"])
    return args


def _split_curl_response(raw: str) -> tuple[str, int]:
    if "\n" in raw:
        raw_body, raw_code = raw.rsplit("\n", 1)
    else:
        raw_body, raw_code = raw, "0"
    try:
        return raw_body, int(raw_code.strip() or 0)
    except ValueError:
        return raw_body, 0


def _trace_and_write_error(
    *,
    trace_metadata: dict[str, Any] | None,
    provider_name: str,
    url: str,
    status_code: int,
    raw_body: str,
    trace_id: str,
    max_chars: int,
    env_owner: EnvironmentConfig,
    write_event,
) -> None:
    trace_http_json_response(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        status_code=status_code,
        body_text=raw_body,
        transport="curl",
        env=env_owner,
    )
    write_event(
        {
            "event": "error",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "status": status_code,
            "error": raw_body[:max_chars],
            "transport": "curl",
        }
    )


def _raise_status_error(
    *,
    provider_name: str,
    status_code: int,
    raw_body: str,
) -> None:
    detail = raw_body.strip() or "(no response body)"
    if status_code in {401, 403}:
        raise LLMCtlError("AUTH_ERROR", f"{provider_name} auth failed: {detail}")
    if status_code == 429:
        raise LLMCtlError("RATE_LIMITED", f"{provider_name} rate limited: {detail}")
    if status_code in {408, 504}:
        raise LLMCtlError("TIMEOUT", f"{provider_name} timeout: {detail}")
    raise LLMCtlError(
        "PROVIDER_ERROR",
        f"{provider_name} request failed with HTTP {status_code}: {detail}",
    )


def _parse_curl_response(
    *,
    trace_metadata: dict[str, Any] | None,
    provider_name: str,
    url: str,
    status_code: int,
    raw_body: str,
    trace_id: str,
    max_chars: int,
    env_owner: EnvironmentConfig,
    write_event,
) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        parse_error = f"{type(exc).__name__}: {exc}"
        trace_http_json_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            body_text=raw_body,
            transport="curl",
            parse_error=parse_error,
            env=env_owner,
        )
        write_event(
            {
                "event": "error",
                "provider": provider_name,
                "trace_id": trace_id,
                "url": url,
                "error": "invalid_json_response",
                "raw": raw_body[:max_chars],
                "transport": "curl",
            }
        )
        raise LLMCtlError(
            "PROVIDER_ERROR", f"{provider_name} response was not valid JSON"
        ) from exc

    trace_http_json_response(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        status_code=status_code,
        body_text=raw_body,
        transport="curl",
        parsed_json=parsed,
        env=env_owner,
    )
    if isinstance(parsed, dict):
        return parsed
    write_event(
        {
            "event": "error",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "error": "response_not_object",
            "raw": raw_body[:max_chars],
            "transport": "curl",
        }
    )
    raise LLMCtlError("PROVIDER_ERROR", f"{provider_name} response was not an object")
