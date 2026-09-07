import socket
import sys
import time
from collections.abc import Iterator, Mapping
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from openminion.base.config.env import EnvironmentConfig
from ...errors import LLMCtlError
from .client import ProviderHTTPClient
from .error_facts import openai_error_facts, openai_error_message
from .http import (
    _safe_http_error_body,
    response_request_id,
    with_default_user_agent,
)
from .payload import serialize_json_payload
from .telemetry import (
    elapsed_ms as _elapsed_ms,
    emit_transport_performance as _emit_transport_performance,
    emit_transport_timeout_counter as _emit_transport_timeout_counter,
)
from .trace import trace_http_json_request, trace_http_sse_response


def _sse_error_facts(error: BaseException | None) -> dict[str, Any]:
    if error is None or isinstance(error, GeneratorExit):
        return {}
    facts: dict[str, Any] = {"type": type(error).__name__}
    if isinstance(error, LLMCtlError):
        facts["code"] = error.code
        category = str(error.details.get("category") or "").strip()
        if category:
            facts["category"] = category
    return facts


def _raise_sse_http_error(
    exc: urllib_error.HTTPError,
    *,
    provider_name: str,
    facts: dict[str, Any],
) -> None:
    message = openai_error_message(facts, status_code=int(exc.code))
    if exc.code in {401, 403}:
        raise LLMCtlError(
            "AUTH_ERROR", f"{provider_name} auth failed: {message}", facts
        ) from exc
    if exc.code == 429:
        raise LLMCtlError(
            "RATE_LIMITED", f"{provider_name} rate limited: {message}", facts
        ) from exc
    if exc.code in {408, 504}:
        raise LLMCtlError(
            "TIMEOUT", f"{provider_name} timeout: {message}", facts
        ) from exc
    raise LLMCtlError(
        "PROVIDER_ERROR",
        f"{provider_name} request failed with HTTP {exc.code}: {message}",
        facts,
    ) from exc


def _finalize_sse_request(
    *,
    error: BaseException | None,
    telemetryctl: Any | None,
    provider_name: str,
    trace_metadata: dict[str, Any] | None,
    request_build_ms: int,
    response_open_ms: int | None,
    first_event_ms: int | None,
    total_started: float,
    request_bytes: int,
    response_bytes: int,
    transport_name: str,
    url: str,
    status_code: int,
    request_id: str,
    consumed_lines: list[str],
    complete: bool,
    env: EnvironmentConfig | Mapping[str, object] | None,
) -> None:
    reason = ""
    status = "ok" if complete else "error"
    if isinstance(error, GeneratorExit):
        status = "cancelled"
        reason = "consumer_closed"
    elif isinstance(error, LLMCtlError):
        reason = error.code
        if error.code == "TIMEOUT":
            _emit_transport_timeout_counter(
                telemetryctl,
                provider_name=provider_name,
                method="POST",
                reason=reason,
                trace_metadata=trace_metadata,
            )
    elif error is not None:
        reason = type(error).__name__
    _emit_transport_performance(
        telemetryctl,
        provider_name=provider_name,
        method="POST",
        status=status,
        request_build_ms=request_build_ms,
        response_open_ms=response_open_ms,
        first_event_ms=first_event_ms,
        total_ms=_elapsed_ms(total_started),
        request_bytes=request_bytes,
        response_bytes=response_bytes,
        reason=reason,
        transport=transport_name,
        trace_metadata=trace_metadata,
    )
    trace_http_sse_response(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        status_code=status_code,
        request_id=request_id,
        lines=consumed_lines,
        complete=complete,
        transport=transport_name,
        error=_sse_error_facts(error),
        env=env,
    )


def iter_sse_post_lines(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: int,
    provider_name: str,
    trace_metadata: dict[str, Any] | None = None,
    transport: str = "urllib_stream",
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    http_client: ProviderHTTPClient | None = None,
    response_metadata: dict[str, str] | None = None,
    telemetryctl: Any | None = None,
) -> Iterator[str]:
    total_started = time.perf_counter()
    request_build_started = time.perf_counter()
    serialized_payload = serialize_json_payload(payload)
    request_headers = with_default_user_agent(headers)
    transport_name = http_client.transport_name if http_client else transport
    consumed_lines: list[str] = []
    status_code, request_id = 0, ""
    complete, response_bytes = False, 0
    response_open_ms: int | None = None
    first_event_ms: int | None = None
    if response_metadata is not None:
        response_metadata.clear()
    trace_http_json_request(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        body_json=serialized_payload.body_json,
        payload=serialized_payload.payload,
        headers=request_headers,
        timeout_seconds=timeout_seconds,
        transport=transport_name,
        env=env,
    )
    req_obj = urllib_request.Request(
        url,
        data=serialized_payload.body_bytes,
        headers=request_headers,
        method="POST",
    )
    request_build_ms = _elapsed_ms(request_build_started)
    request_started = time.perf_counter()
    try:
        open_url = http_client.urlopen if http_client else urllib_request.urlopen
        with open_url(req_obj, timeout=float(timeout_seconds)) as response:
            response_open_ms = _elapsed_ms(request_started)
            status_code = int(getattr(response, "status", 200) or 200)
            request_id = response_request_id(getattr(response, "headers", None))
            if response_metadata is not None and request_id:
                response_metadata["request_id"] = request_id
            for raw_line in response:
                if first_event_ms is None:
                    first_event_ms = _elapsed_ms(request_started)
                response_bytes += len(raw_line)
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                consumed_lines.append(line)
                yield line
            complete = True
    except urllib_error.HTTPError as exc:
        status_code = int(exc.code)
        detail = _safe_http_error_body(exc)
        facts = openai_error_facts(
            detail,
            status_code=int(exc.code),
            request_id=str((exc.headers or {}).get("X-Request-ID") or ""),
        )
        request_id = str(facts.get("request_id") or "")
        _raise_sse_http_error(exc, provider_name=provider_name, facts=facts)
    except urllib_error.URLError as exc:
        reason = str(exc.reason)
        if isinstance(exc.reason, socket.timeout) or "timed out" in reason.lower():
            raise LLMCtlError(
                "TIMEOUT", f"{provider_name} request timed out: {reason}"
            ) from exc
        raise LLMCtlError(
            "PROVIDER_ERROR", f"{provider_name} request failed: {reason}"
        ) from exc
    finally:
        _finalize_sse_request(
            error=sys.exc_info()[1],
            telemetryctl=telemetryctl,
            provider_name=provider_name,
            trace_metadata=trace_metadata,
            request_build_ms=request_build_ms,
            response_open_ms=response_open_ms,
            first_event_ms=first_event_ms,
            total_started=total_started,
            request_bytes=serialized_payload.byte_count,
            response_bytes=response_bytes,
            transport_name=transport_name,
            url=url,
            status_code=status_code,
            request_id=request_id,
            consumed_lines=consumed_lines,
            complete=complete,
            env=env,
        )
