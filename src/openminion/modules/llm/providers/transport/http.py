import json
import logging
import socket
import time
import uuid
from typing import Any, Dict, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.modules.llm.constants import DEFAULT_HTTP_USER_AGENT
from openminion.modules.telemetry.events.module import (
    emit_module_counter,
    emit_module_operation,
    emit_module_telemetry,
)
from ...errors import LLMCtlError
from .curl import curl_json_post
from .debug import (
    llm_debug_max_chars,
    truncate_debug_value,
    write_llm_debug_event,
)
from .payload import serialize_json_payload
from .trace import trace_http_json_request, trace_http_json_response

_LOG = logging.getLogger(__name__)


def _safe_http_error_body(exc: urllib_error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:
        return "(no response body)"


def with_default_user_agent(headers: Dict[str, str]) -> Dict[str, str]:
    normalized = {str(key): str(value) for key, value in headers.items()}
    for key, value in normalized.items():
        if key.strip().lower() == "user-agent" and value.strip():
            return normalized
    normalized["User-Agent"] = DEFAULT_HTTP_USER_AGENT
    return normalized


def _emit_transport_timeout_counter(
    telemetryctl: Any | None,
    *,
    provider_name: str,
    method: str,
    reason: str,
) -> None:
    if telemetryctl is None:
        return

    def _emit(method_name: str, *args: Any, **kwargs: Any) -> bool:
        return bool(
            emit_module_telemetry(
                telemetryctl,
                method_name,
                *args,
                logger=_LOG,
                **kwargs,
            )
        )

    emit_module_counter(
        emit_module_telemetry_fn=_emit,
        session_id="llm",
        turn_id="transport",
        module_id="openminion-llm",
        counter_name="llm_transport_timeout",
        value=1.0,
        status="error",
        extra={
            "provider": str(provider_name or "").strip(),
            "method": str(method or "").strip().upper(),
            "reason": str(reason or "").strip(),
        },
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _emit_transport_performance(
    telemetryctl: Any | None,
    *,
    provider_name: str,
    method: str,
    status: str,
    request_build_ms: int | None = None,
    round_trip_ms: int | None = None,
    parse_ms: int | None = None,
    total_ms: int | None = None,
    request_bytes: int | None = None,
    response_bytes: int | None = None,
    retry_count: int = 0,
    reason: str = "",
) -> None:
    if telemetryctl is None:
        return

    def _emit(method_name: str, *args: Any, **kwargs: Any) -> bool:
        return bool(
            emit_module_telemetry(
                telemetryctl,
                method_name,
                *args,
                logger=_LOG,
                **kwargs,
            )
        )

    extra = {
        "provider": str(provider_name or "").strip(),
        "method": str(method or "").strip().upper(),
        "transport": "urllib",
        "request_build_ms": request_build_ms,
        "provider_round_trip_ms": round_trip_ms,
        "parse_ms": parse_ms,
        "total_ms": total_ms,
        "request_bytes": request_bytes,
        "response_bytes": response_bytes,
        "retry_count": int(retry_count),
    }
    if reason:
        extra["reason"] = str(reason or "").strip()
    emit_module_operation(
        emit_module_telemetry_fn=_emit,
        session_id="llm",
        turn_id="transport",
        module_id="openminion-llm",
        operation=f"http_json_{str(method or '').strip().lower()}",
        status=status,
        extra=extra,
    )


def _should_use_curl_fallback(reason: str) -> bool:
    lowered = reason.lower()
    return any(
        token in lowered
        for token in (
            "nodename nor servname provided",
            "name or service not known",
            "temporary failure in name resolution",
        )
    )


def http_json_get(
    *,
    url: str,
    headers: Dict[str, str],
    timeout_seconds: int,
    provider_name: str,
    trace_metadata: Dict[str, Any] | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    telemetryctl: Any | None = None,
) -> Dict[str, Any]:
    """GET a JSON payload from a provider URL."""
    total_started = time.perf_counter()
    env_owner = resolve_environment_config(env=env)
    request_build_started = time.perf_counter()
    request_headers = with_default_user_agent(headers)

    def _write(event: Dict[str, Any]) -> None:
        write_llm_debug_event(event, env=env_owner)

    trace_id = uuid.uuid4().hex
    max_chars = llm_debug_max_chars(env=env_owner)
    _write(
        {
            "event": "request",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "timeout_seconds": timeout_seconds,
            "method": "GET",
        }
    )

    trace_http_json_request(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        body_json="",
        payload=None,
        headers=request_headers,
        timeout_seconds=timeout_seconds,
        transport="urllib",
        method="GET",
        env=env_owner,
    )
    request_obj = urllib_request.Request(
        url,
        headers=request_headers,
        method="GET",
    )
    request_build_ms = _elapsed_ms(request_build_started)
    round_trip_ms: int | None = None
    parse_ms: int | None = None
    response_bytes: int | None = None

    try:
        round_trip_started = time.perf_counter()
        with urllib_request.urlopen(
            request_obj, timeout=float(timeout_seconds)
        ) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            raw = response.read().decode("utf-8")
        round_trip_ms = _elapsed_ms(round_trip_started)
        response_bytes = len(raw.encode("utf-8"))
    except urllib_error.HTTPError as exc:
        detail = _safe_http_error_body(exc)
        response_bytes = len(detail.encode("utf-8"))
        trace_http_json_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=int(getattr(exc, "code", 0) or 0),
            body_text=detail,
            transport="urllib",
            env=env_owner,
        )
        _write(
            {
                "event": "error",
                "provider": provider_name,
                "trace_id": trace_id,
                "url": url,
                "status": getattr(exc, "code", 0),
                "error": detail[:max_chars],
            }
        )
        if exc.code in {401, 403}:
            _emit_transport_performance(
                telemetryctl,
                provider_name=provider_name,
                method="GET",
                status="error",
                request_build_ms=request_build_ms,
                round_trip_ms=round_trip_ms,
                parse_ms=parse_ms,
                total_ms=_elapsed_ms(total_started),
                response_bytes=response_bytes,
                reason=f"http_{exc.code}",
            )
            raise LLMCtlError(
                "AUTH_ERROR",
                f"{provider_name} auth failed: {detail}",
                details={
                    "provider": provider_name,
                    "status_code": int(exc.code),
                    "url": url,
                    "response_text": detail,
                },
            ) from exc
        if exc.code == 429:
            _emit_transport_performance(
                telemetryctl,
                provider_name=provider_name,
                method="GET",
                status="error",
                request_build_ms=request_build_ms,
                round_trip_ms=round_trip_ms,
                parse_ms=parse_ms,
                total_ms=_elapsed_ms(total_started),
                response_bytes=response_bytes,
                reason=f"http_{exc.code}",
            )
            raise LLMCtlError(
                "RATE_LIMITED",
                f"{provider_name} rate limited: {detail}",
                details={
                    "provider": provider_name,
                    "status_code": int(exc.code),
                    "url": url,
                    "response_text": detail,
                },
            ) from exc
        if exc.code in {408, 504}:
            _emit_transport_timeout_counter(
                telemetryctl,
                provider_name=provider_name,
                method="GET",
                reason=f"http_{exc.code}",
            )
            _emit_transport_performance(
                telemetryctl,
                provider_name=provider_name,
                method="GET",
                status="error",
                request_build_ms=request_build_ms,
                round_trip_ms=round_trip_ms,
                parse_ms=parse_ms,
                total_ms=_elapsed_ms(total_started),
                response_bytes=response_bytes,
                reason=f"http_{exc.code}",
            )
            raise LLMCtlError(
                "TIMEOUT",
                f"{provider_name} timeout: {detail}",
                details={
                    "provider": provider_name,
                    "status_code": int(exc.code),
                    "url": url,
                    "response_text": detail,
                },
            ) from exc
        _emit_transport_performance(
            telemetryctl,
            provider_name=provider_name,
            method="GET",
            status="error",
            request_build_ms=request_build_ms,
            round_trip_ms=round_trip_ms,
            parse_ms=parse_ms,
            total_ms=_elapsed_ms(total_started),
            response_bytes=response_bytes,
            reason=f"http_{exc.code}",
        )
        raise LLMCtlError(
            "PROVIDER_ERROR",
            f"{provider_name} request failed with HTTP {exc.code}: {detail}",
            details={
                "provider": provider_name,
                "status_code": int(exc.code),
                "url": url,
                "response_text": detail,
            },
        ) from exc
    except urllib_error.URLError as exc:
        reason = str(exc.reason)
        _write(
            {
                "event": "error",
                "provider": provider_name,
                "trace_id": trace_id,
                "url": url,
                "error": reason[:max_chars],
            }
        )
        if isinstance(exc.reason, socket.timeout) or "timed out" in reason.lower():
            _emit_transport_timeout_counter(
                telemetryctl,
                provider_name=provider_name,
                method="GET",
                reason=reason,
            )
            _emit_transport_performance(
                telemetryctl,
                provider_name=provider_name,
                method="GET",
                status="error",
                request_build_ms=request_build_ms,
                round_trip_ms=round_trip_ms,
                parse_ms=parse_ms,
                total_ms=_elapsed_ms(total_started),
                reason=reason,
            )
            raise LLMCtlError(
                "TIMEOUT",
                f"{provider_name} request timed out: {reason}",
                details={"provider": provider_name, "url": url},
            ) from exc
        _emit_transport_performance(
            telemetryctl,
            provider_name=provider_name,
            method="GET",
            status="error",
            request_build_ms=request_build_ms,
            round_trip_ms=round_trip_ms,
            parse_ms=parse_ms,
            total_ms=_elapsed_ms(total_started),
            reason=reason,
        )
        raise LLMCtlError(
            "PROVIDER_ERROR",
            f"{provider_name} request failed: {reason}",
            details={"provider": provider_name, "url": url},
        ) from exc

    parsed: dict[str, Any] | None = None
    parse_error = ""
    try:
        parse_started = time.perf_counter()
        parsed = json.loads(raw)
        parse_ms = _elapsed_ms(parse_started)
    except json.JSONDecodeError as exc:
        parse_ms = _elapsed_ms(parse_started)
        parse_error = f"{type(exc).__name__}: {exc}"
        trace_http_json_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            body_text=raw,
            transport="urllib",
            parse_error=parse_error,
            env=env_owner,
        )
        _write(
            {
                "event": "error",
                "provider": provider_name,
                "trace_id": trace_id,
                "url": url,
                "error": "invalid_json_response",
                "raw": raw[:max_chars],
            }
        )
        _emit_transport_performance(
            telemetryctl,
            provider_name=provider_name,
            method="GET",
            status="error",
            request_build_ms=request_build_ms,
            round_trip_ms=round_trip_ms,
            parse_ms=parse_ms,
            total_ms=_elapsed_ms(total_started),
            response_bytes=response_bytes,
            reason="invalid_json_response",
        )
        raise LLMCtlError(
            "PROVIDER_ERROR", f"{provider_name} response was not valid JSON"
        ) from exc
    else:
        trace_http_json_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            body_text=raw,
            transport="urllib",
            parsed_json=parsed,
            env=env_owner,
        )

    if not isinstance(parsed, dict):
        _write(
            {
                "event": "error",
                "provider": provider_name,
                "trace_id": trace_id,
                "url": url,
                "error": "response_not_object",
                "raw": raw[:max_chars],
            }
        )
        _emit_transport_performance(
            telemetryctl,
            provider_name=provider_name,
            method="GET",
            status="error",
            request_build_ms=request_build_ms,
            round_trip_ms=round_trip_ms,
            parse_ms=parse_ms,
            total_ms=_elapsed_ms(total_started),
            response_bytes=response_bytes,
            reason="response_not_object",
        )
        raise LLMCtlError(
            "PROVIDER_ERROR", f"{provider_name} response was not an object"
        )

    _write(
        {
            "event": "response",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "payload": truncate_debug_value(parsed, max_chars),
        }
    )
    _emit_transport_performance(
        telemetryctl,
        provider_name=provider_name,
        method="GET",
        status="ok",
        request_build_ms=request_build_ms,
        round_trip_ms=round_trip_ms,
        parse_ms=parse_ms,
        total_ms=_elapsed_ms(total_started),
        response_bytes=response_bytes,
    )
    return parsed


def http_json_post(
    *,
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout_seconds: int,
    provider_name: str,
    trace_metadata: Dict[str, Any] | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    telemetryctl: Any | None = None,
) -> Dict[str, Any]:
    total_started = time.perf_counter()
    env_owner = resolve_environment_config(env=env)
    request_build_started = time.perf_counter()
    request_headers = with_default_user_agent(headers)

    def _write(event: Dict[str, Any]) -> None:
        write_llm_debug_event(event, env=env_owner)

    trace_id = uuid.uuid4().hex
    max_chars = llm_debug_max_chars(env=env_owner)
    _write(
        {
            "event": "request",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "timeout_seconds": timeout_seconds,
            "payload": truncate_debug_value(payload, max_chars),
        }
    )

    serialized_payload = serialize_json_payload(payload)

    trace_http_json_request(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        body_json=serialized_payload.body_json,
        payload=serialized_payload.payload,
        headers=request_headers,
        timeout_seconds=timeout_seconds,
        transport="urllib",
        env=env_owner,
    )
    request_obj = urllib_request.Request(
        url,
        data=serialized_payload.body_bytes,
        headers=request_headers,
        method="POST",
    )
    request_build_ms = _elapsed_ms(request_build_started)
    round_trip_ms: int | None = None
    parse_ms: int | None = None
    response_bytes: int | None = None

    try:
        round_trip_started = time.perf_counter()
        with urllib_request.urlopen(
            request_obj, timeout=float(timeout_seconds)
        ) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            raw = response.read().decode("utf-8")
        round_trip_ms = _elapsed_ms(round_trip_started)
        response_bytes = len(raw.encode("utf-8"))
    except urllib_error.HTTPError as exc:
        detail = _safe_http_error_body(exc)
        response_bytes = len(detail.encode("utf-8"))
        trace_http_json_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=int(getattr(exc, "code", 0) or 0),
            body_text=detail,
            transport="urllib",
            env=env_owner,
        )
        _write(
            {
                "event": "error",
                "provider": provider_name,
                "trace_id": trace_id,
                "url": url,
                "status": getattr(exc, "code", 0),
                "error": detail[:max_chars],
            }
        )
        if exc.code in {401, 403}:
            _emit_transport_performance(
                telemetryctl,
                provider_name=provider_name,
                method="POST",
                status="error",
                request_build_ms=request_build_ms,
                round_trip_ms=round_trip_ms,
                parse_ms=parse_ms,
                total_ms=_elapsed_ms(total_started),
                request_bytes=serialized_payload.byte_count,
                response_bytes=response_bytes,
                reason=f"http_{exc.code}",
            )
            raise LLMCtlError(
                "AUTH_ERROR",
                f"{provider_name} auth failed: {detail}",
                details={
                    "provider": provider_name,
                    "status_code": int(exc.code),
                    "url": url,
                    "response_text": detail,
                },
            ) from exc
        if exc.code == 429:
            _emit_transport_performance(
                telemetryctl,
                provider_name=provider_name,
                method="POST",
                status="error",
                request_build_ms=request_build_ms,
                round_trip_ms=round_trip_ms,
                parse_ms=parse_ms,
                total_ms=_elapsed_ms(total_started),
                request_bytes=serialized_payload.byte_count,
                response_bytes=response_bytes,
                reason=f"http_{exc.code}",
            )
            raise LLMCtlError(
                "RATE_LIMITED",
                f"{provider_name} rate limited: {detail}",
                details={
                    "provider": provider_name,
                    "status_code": int(exc.code),
                    "url": url,
                    "response_text": detail,
                },
            ) from exc
        if exc.code in {408, 504}:
            _emit_transport_timeout_counter(
                telemetryctl,
                provider_name=provider_name,
                method="POST",
                reason=f"http_{exc.code}",
            )
            _emit_transport_performance(
                telemetryctl,
                provider_name=provider_name,
                method="POST",
                status="error",
                request_build_ms=request_build_ms,
                round_trip_ms=round_trip_ms,
                parse_ms=parse_ms,
                total_ms=_elapsed_ms(total_started),
                request_bytes=serialized_payload.byte_count,
                response_bytes=response_bytes,
                reason=f"http_{exc.code}",
            )
            raise LLMCtlError(
                "TIMEOUT",
                f"{provider_name} timeout: {detail}",
                details={
                    "provider": provider_name,
                    "status_code": int(exc.code),
                    "url": url,
                    "response_text": detail,
                },
            ) from exc
        _emit_transport_performance(
            telemetryctl,
            provider_name=provider_name,
            method="POST",
            status="error",
            request_build_ms=request_build_ms,
            round_trip_ms=round_trip_ms,
            parse_ms=parse_ms,
            total_ms=_elapsed_ms(total_started),
            request_bytes=serialized_payload.byte_count,
            response_bytes=response_bytes,
            reason=f"http_{exc.code}",
        )
        raise LLMCtlError(
            "PROVIDER_ERROR",
            f"{provider_name} request failed with HTTP {exc.code}: {detail}",
            details={
                "provider": provider_name,
                "status_code": int(exc.code),
                "url": url,
                "response_text": detail,
            },
        ) from exc
    except urllib_error.URLError as exc:
        reason = str(exc.reason)
        _write(
            {
                "event": "error",
                "provider": provider_name,
                "trace_id": trace_id,
                "url": url,
                "error": reason[:max_chars],
            }
        )
        if isinstance(exc.reason, socket.timeout) or "timed out" in reason.lower():
            _emit_transport_timeout_counter(
                telemetryctl,
                provider_name=provider_name,
                method="POST",
                reason=reason,
            )
            _emit_transport_performance(
                telemetryctl,
                provider_name=provider_name,
                method="POST",
                status="error",
                request_build_ms=request_build_ms,
                round_trip_ms=round_trip_ms,
                parse_ms=parse_ms,
                total_ms=_elapsed_ms(total_started),
                request_bytes=serialized_payload.byte_count,
                reason=reason,
            )
            raise LLMCtlError(
                "TIMEOUT",
                f"{provider_name} request timed out: {reason}",
                details={"provider": provider_name, "url": url},
            ) from exc
        if _should_use_curl_fallback(reason):
            _emit_transport_performance(
                telemetryctl,
                provider_name=provider_name,
                method="POST",
                status="error",
                request_build_ms=request_build_ms,
                round_trip_ms=round_trip_ms,
                parse_ms=parse_ms,
                total_ms=_elapsed_ms(total_started),
                request_bytes=serialized_payload.byte_count,
                retry_count=1,
                reason=reason,
            )
            return curl_json_post(
                url=url,
                payload=serialized_payload.payload,
                body_json=serialized_payload.body_json,
                headers=headers,
                timeout_seconds=timeout_seconds,
                provider_name=provider_name,
                reason=reason,
                with_default_user_agent_fn=with_default_user_agent,
                trace_metadata=trace_metadata,
                env=env_owner,
            )
        _emit_transport_performance(
            telemetryctl,
            provider_name=provider_name,
            method="POST",
            status="error",
            request_build_ms=request_build_ms,
            round_trip_ms=round_trip_ms,
            parse_ms=parse_ms,
            total_ms=_elapsed_ms(total_started),
            request_bytes=serialized_payload.byte_count,
            reason=reason,
        )
        raise LLMCtlError(
            "PROVIDER_ERROR",
            f"{provider_name} request failed: {reason}",
            details={"provider": provider_name, "url": url},
        ) from exc

    parsed: dict[str, Any] | None = None
    parse_error = ""
    try:
        parse_started = time.perf_counter()
        parsed = json.loads(raw)
        parse_ms = _elapsed_ms(parse_started)
    except json.JSONDecodeError as exc:
        parse_ms = _elapsed_ms(parse_started)
        parse_error = f"{type(exc).__name__}: {exc}"
        trace_http_json_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            body_text=raw,
            transport="urllib",
            parse_error=parse_error,
            env=env_owner,
        )
        _write(
            {
                "event": "error",
                "provider": provider_name,
                "trace_id": trace_id,
                "url": url,
                "error": "invalid_json_response",
                "raw": raw[:max_chars],
            }
        )
        _emit_transport_performance(
            telemetryctl,
            provider_name=provider_name,
            method="POST",
            status="error",
            request_build_ms=request_build_ms,
            round_trip_ms=round_trip_ms,
            parse_ms=parse_ms,
            total_ms=_elapsed_ms(total_started),
            request_bytes=serialized_payload.byte_count,
            response_bytes=response_bytes,
            reason="invalid_json_response",
        )
        raise LLMCtlError(
            "PROVIDER_ERROR", f"{provider_name} response was not valid JSON"
        ) from exc
    else:
        trace_http_json_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            body_text=raw,
            transport="urllib",
            parsed_json=parsed,
            env=env_owner,
        )

    if not isinstance(parsed, dict):
        _write(
            {
                "event": "error",
                "provider": provider_name,
                "trace_id": trace_id,
                "url": url,
                "error": "response_not_object",
                "raw": raw[:max_chars],
            }
        )
        _emit_transport_performance(
            telemetryctl,
            provider_name=provider_name,
            method="POST",
            status="error",
            request_build_ms=request_build_ms,
            round_trip_ms=round_trip_ms,
            parse_ms=parse_ms,
            total_ms=_elapsed_ms(total_started),
            request_bytes=serialized_payload.byte_count,
            response_bytes=response_bytes,
            reason="response_not_object",
        )
        raise LLMCtlError(
            "PROVIDER_ERROR", f"{provider_name} response was not an object"
        )

    _write(
        {
            "event": "response",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "payload": truncate_debug_value(parsed, max_chars),
        }
    )
    _emit_transport_performance(
        telemetryctl,
        provider_name=provider_name,
        method="POST",
        status="ok",
        request_build_ms=request_build_ms,
        round_trip_ms=round_trip_ms,
        parse_ms=parse_ms,
        total_ms=_elapsed_ms(total_started),
        request_bytes=serialized_payload.byte_count,
        response_bytes=response_bytes,
    )
    return parsed
