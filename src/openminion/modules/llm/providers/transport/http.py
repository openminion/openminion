import json
import logging
import socket
import time
import uuid
from functools import partial
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
from .client import ProviderHTTPClient
from .curl import curl_json_post
from .debug import (
    llm_debug_max_chars,
    truncate_debug_value,
    write_llm_debug_event,
)
from .error_facts import (
    malformed_response_facts,
    openai_error_facts,
    openai_error_message,
)
from .payload import serialize_json_payload
from .trace import trace_http_json_request, trace_http_json_response

_LOG = logging.getLogger(__name__)


def _safe_http_error_body(exc: urllib_error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:
        return "(no response body)"


def _http_error_details(
    exc: urllib_error.HTTPError,
) -> tuple[str, dict[str, Any], str]:
    body = _safe_http_error_body(exc)
    facts = openai_error_facts(
        body,
        status_code=int(exc.code),
        request_id=str((exc.headers or {}).get("X-Request-ID") or ""),
    )
    return body, facts, openai_error_message(facts, status_code=int(exc.code))


def _record_malformed_response(
    *,
    trace_metadata: Dict[str, Any] | None,
    provider_name: str,
    url: str,
    status_code: int,
    raw: str,
    error: str,
    transport: str,
    trace_id: str,
    env: EnvironmentConfig,
    write_event: Any,
    parse_error: str = "",
) -> None:
    facts = malformed_response_facts(raw, status_code=status_code, error=error)
    trace_http_json_response(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        status_code=status_code,
        body_text=json.dumps(facts),
        transport=transport,
        parse_error=parse_error,
        env=env,
    )
    write_event(
        {
            "event": "error",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "error": error,
            "response_bytes": facts["response_bytes"],
        }
    )


def with_default_user_agent(headers: Dict[str, str]) -> Dict[str, str]:
    normalized = {str(key): str(value) for key, value in headers.items()}
    for key, value in normalized.items():
        if key.strip().lower() == "user-agent" and value.strip():
            return normalized
    normalized["User-Agent"] = DEFAULT_HTTP_USER_AGENT
    return normalized


def response_request_id(headers: Mapping[str, Any] | None) -> str:
    if headers is None:
        return ""
    for key, value in headers.items():
        if str(key).strip().lower() == "x-request-id":
            return str(value or "").strip()
    return ""


def _capture_response_request_id(
    response_metadata: Dict[str, str] | None,
    facts: Mapping[str, Any],
) -> str:
    request_id = str(facts.get("request_id") or "").strip()
    if response_metadata is not None and request_id:
        response_metadata["request_id"] = request_id
    return request_id


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
            "provider": provider_name.strip(),
            "method": method.strip().upper(),
            "reason": reason.strip(),
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
    transport: str = "urllib",
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
        "provider": provider_name.strip(),
        "method": method.strip().upper(),
        "transport": transport,
        "request_build_ms": request_build_ms,
        "provider_round_trip_ms": round_trip_ms,
        "parse_ms": parse_ms,
        "total_ms": total_ms,
        "request_bytes": request_bytes,
        "response_bytes": response_bytes,
        "retry_count": retry_count,
    }
    if reason:
        extra["reason"] = reason.strip()
    emit_module_operation(
        emit_module_telemetry_fn=_emit,
        session_id="llm",
        turn_id="transport",
        module_id="openminion-llm",
        operation=f"http_json_{method.strip().lower()}",
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


def _read_http_response(
    request: urllib_request.Request,
    *,
    timeout_seconds: int,
    http_client: ProviderHTTPClient | None,
) -> tuple[int, str, str]:
    open_url = http_client.urlopen if http_client else urllib_request.urlopen
    with open_url(request, timeout=float(timeout_seconds)) as response:
        status_code = int(getattr(response, "status", 200) or 200)
        request_id = response_request_id(getattr(response, "headers", None))
        raw = response.read().decode("utf-8")
    return status_code, raw, request_id


def http_json_get(
    *,
    url: str,
    headers: Dict[str, str],
    timeout_seconds: int,
    provider_name: str,
    trace_metadata: Dict[str, Any] | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    telemetryctl: Any | None = None,
    http_client: ProviderHTTPClient | None = None,
    response_metadata: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """GET a JSON payload from a provider URL."""
    total_started = time.perf_counter()
    env_owner = resolve_environment_config(env=env)
    request_build_started = time.perf_counter()
    request_headers = with_default_user_agent(headers)
    if response_metadata is not None:
        response_metadata.clear()
    transport_name = http_client.transport_name if http_client else "urllib"
    emit_performance = partial(_emit_transport_performance, transport=transport_name)

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
        transport=transport_name,
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
        status_code, raw, request_id = _read_http_response(
            request_obj,
            timeout_seconds=timeout_seconds,
            http_client=http_client,
        )
        if response_metadata is not None and request_id:
            response_metadata["request_id"] = request_id
        round_trip_ms = _elapsed_ms(round_trip_started)
        response_bytes = len(raw.encode("utf-8"))
    except urllib_error.HTTPError as exc:
        detail, error_facts, safe_detail = _http_error_details(exc)
        request_id = _capture_response_request_id(response_metadata, error_facts)
        response_bytes = len(detail.encode("utf-8"))
        trace_http_json_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=int(getattr(exc, "code", 0) or 0),
            body_text=json.dumps(error_facts),
            transport=transport_name,
            env=env_owner,
        )
        _write(
            {
                "event": "error",
                "provider": provider_name,
                "trace_id": trace_id,
                "url": url,
                "status": getattr(exc, "code", 0),
                "error": safe_detail[:max_chars],
                **({"request_id": request_id} if request_id else {}),
            }
        )
        if exc.code in {401, 403}:
            emit_performance(
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
                f"{provider_name} auth failed: {safe_detail}",
                details={
                    "provider": provider_name,
                    "url": url,
                    **error_facts,
                },
            ) from exc
        if exc.code == 429:
            emit_performance(
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
                f"{provider_name} rate limited: {safe_detail}",
                details={
                    "provider": provider_name,
                    "url": url,
                    **error_facts,
                },
            ) from exc
        if exc.code in {408, 504}:
            _emit_transport_timeout_counter(
                telemetryctl,
                provider_name=provider_name,
                method="GET",
                reason=f"http_{exc.code}",
            )
            emit_performance(
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
                f"{provider_name} timeout: {safe_detail}",
                details={
                    "provider": provider_name,
                    "url": url,
                    **error_facts,
                },
            ) from exc
        emit_performance(
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
            f"{provider_name} request failed with HTTP {exc.code}: {safe_detail}",
            details={
                "provider": provider_name,
                "url": url,
                **error_facts,
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
            emit_performance(
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
        emit_performance(
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

    try:
        parse_started = time.perf_counter()
        parsed = json.loads(raw)
        parse_ms = _elapsed_ms(parse_started)
    except json.JSONDecodeError as exc:
        parse_ms = _elapsed_ms(parse_started)
        parse_error = f"{type(exc).__name__}: {exc}"
        _record_malformed_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            raw=raw,
            error="invalid_json_response",
            transport=transport_name,
            trace_id=trace_id,
            parse_error=parse_error,
            env=env_owner,
            write_event=_write,
        )
        emit_performance(
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
    if not isinstance(parsed, dict):
        _record_malformed_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            raw=raw,
            error="response_not_object",
            transport=transport_name,
            trace_id=trace_id,
            env=env_owner,
            write_event=_write,
        )
        emit_performance(
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

    trace_http_json_response(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        status_code=status_code,
        body_text=raw,
        transport=transport_name,
        parsed_json=parsed,
        env=env_owner,
    )

    _write(
        {
            "event": "response",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "payload": truncate_debug_value(parsed, max_chars),
            **({"request_id": request_id} if request_id else {}),
        }
    )
    emit_performance(
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
    http_client: ProviderHTTPClient | None = None,
    response_metadata: Dict[str, str] | None = None,
    allow_curl_fallback: bool = True,
) -> Dict[str, Any]:
    total_started = time.perf_counter()
    env_owner = resolve_environment_config(env=env)
    request_build_started = time.perf_counter()
    request_headers = with_default_user_agent(headers)
    if response_metadata is not None:
        response_metadata.clear()
    transport_name = http_client.transport_name if http_client else "urllib"
    emit_performance = partial(_emit_transport_performance, transport=transport_name)

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
        transport=transport_name,
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
        status_code, raw, request_id = _read_http_response(
            request_obj,
            timeout_seconds=timeout_seconds,
            http_client=http_client,
        )
        if response_metadata is not None and request_id:
            response_metadata["request_id"] = request_id
        round_trip_ms = _elapsed_ms(round_trip_started)
        response_bytes = len(raw.encode("utf-8"))
    except urllib_error.HTTPError as exc:
        detail, error_facts, safe_detail = _http_error_details(exc)
        request_id = _capture_response_request_id(response_metadata, error_facts)
        response_bytes = len(detail.encode("utf-8"))
        trace_http_json_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=int(getattr(exc, "code", 0) or 0),
            body_text=json.dumps(error_facts),
            transport=transport_name,
            env=env_owner,
        )
        _write(
            {
                "event": "error",
                "provider": provider_name,
                "trace_id": trace_id,
                "url": url,
                "status": getattr(exc, "code", 0),
                "error": safe_detail[:max_chars],
                **({"request_id": request_id} if request_id else {}),
            }
        )
        if exc.code in {401, 403}:
            emit_performance(
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
                f"{provider_name} auth failed: {safe_detail}",
                details={
                    "provider": provider_name,
                    "url": url,
                    **error_facts,
                },
            ) from exc
        if exc.code == 429:
            emit_performance(
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
                f"{provider_name} rate limited: {safe_detail}",
                details={
                    "provider": provider_name,
                    "url": url,
                    **error_facts,
                },
            ) from exc
        if exc.code in {408, 504}:
            _emit_transport_timeout_counter(
                telemetryctl,
                provider_name=provider_name,
                method="POST",
                reason=f"http_{exc.code}",
            )
            emit_performance(
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
                f"{provider_name} timeout: {safe_detail}",
                details={
                    "provider": provider_name,
                    "url": url,
                    **error_facts,
                },
            ) from exc
        emit_performance(
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
            f"{provider_name} request failed with HTTP {exc.code}: {safe_detail}",
            details={
                "provider": provider_name,
                "url": url,
                **error_facts,
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
            emit_performance(
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
        if allow_curl_fallback and _should_use_curl_fallback(reason):
            emit_performance(
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
        emit_performance(
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

    try:
        parse_started = time.perf_counter()
        parsed = json.loads(raw)
        parse_ms = _elapsed_ms(parse_started)
    except json.JSONDecodeError as exc:
        parse_ms = _elapsed_ms(parse_started)
        parse_error = f"{type(exc).__name__}: {exc}"
        _record_malformed_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            raw=raw,
            error="invalid_json_response",
            transport=transport_name,
            trace_id=trace_id,
            parse_error=parse_error,
            env=env_owner,
            write_event=_write,
        )
        emit_performance(
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
    if not isinstance(parsed, dict):
        _record_malformed_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            raw=raw,
            error="response_not_object",
            transport=transport_name,
            trace_id=trace_id,
            env=env_owner,
            write_event=_write,
        )
        emit_performance(
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

    trace_http_json_response(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        status_code=status_code,
        body_text=raw,
        transport=transport_name,
        parsed_json=parsed,
        env=env_owner,
    )

    _write(
        {
            "event": "response",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "payload": truncate_debug_value(parsed, max_chars),
            **({"request_id": request_id} if request_id else {}),
        }
    )
    emit_performance(
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
