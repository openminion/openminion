import json
import logging
import shutil
import socket
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.modules.llm.constants import DEFAULT_HTTP_USER_AGENT
from openminion.modules.telemetry.events.module import (
    emit_module_counter,
    emit_module_telemetry,
)
from ...errors import LLMCtlError
from .trace import trace_http_json_request, trace_http_json_response

_LOG = logging.getLogger(__name__)


def _llm_debug_enabled(
    provider_name: str,
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> bool:
    env_owner = resolve_environment_config(env=env)
    if not env_owner.openminion_llm_debug:
        return False
    raw_filter = env_owner.openminion_llm_debug_provider
    if not raw_filter:
        return True
    providers = {item.strip() for item in raw_filter.split(",") if item.strip()}
    return str(provider_name or "").strip().lower() in providers


def _llm_debug_dir(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> Path:
    env_owner = resolve_environment_config(env=env)
    configured = env_owner.openminion_llm_debug_dir.strip()
    if configured:
        return Path(configured).expanduser().resolve()
    data_root = env_owner.openminion_data_root.strip()
    if data_root:
        base = Path(data_root).expanduser()
        if not base.is_absolute():
            base = Path.cwd() / base
        return (base / "traces" / "llm").resolve()
    return (Path.cwd() / ".openminion" / "traces" / "llm").resolve()


def _llm_debug_max_chars(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> int:
    env_owner = resolve_environment_config(env=env)
    configured = env_owner.openminion_llm_debug_max_chars
    if configured <= 0:
        return 4000
    return max(200, int(configured))


def _truncate_debug_value(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        return value[:max_chars] + "...[truncated]"
    if isinstance(value, dict):
        return {k: _truncate_debug_value(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate_debug_value(item, max_chars) for item in value]
    return value


def _write_llm_debug_event(
    event: Dict[str, Any],
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> None:
    provider_name = str(event.get("provider") or "").strip()
    if not _llm_debug_enabled(provider_name, env=env):
        return
    try:
        debug_dir = _llm_debug_dir(env=env)
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d")
        filename = debug_dir / f"{provider_name or 'provider'}-{stamp}.jsonl"
        with filename.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True) + "\n")
    except Exception as exc:  # pragma: no cover - debug logging must never crash
        _LOG.debug("LLM debug logging failed: %s", exc)


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
        return emit_module_telemetry(
            telemetryctl,
            method_name,
            *args,
            logger=_LOG,
            **kwargs,
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
    env_owner = resolve_environment_config(env=env)
    request_headers = with_default_user_agent(headers)

    def _write(event: Dict[str, Any]) -> None:
        _write_llm_debug_event(event, env=env_owner)

    trace_id = uuid.uuid4().hex
    max_chars = _llm_debug_max_chars(env=env_owner)
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

    try:
        with urllib_request.urlopen(
            request_obj, timeout=float(timeout_seconds)
        ) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            raw = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        detail = _safe_http_error_body(exc)
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
            raise LLMCtlError(
                "TIMEOUT",
                f"{provider_name} request timed out: {reason}",
                details={"provider": provider_name, "url": url},
            ) from exc
        raise LLMCtlError(
            "PROVIDER_ERROR",
            f"{provider_name} request failed: {reason}",
            details={"provider": provider_name, "url": url},
        ) from exc

    parsed: dict[str, Any] | None = None
    parse_error = ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
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
        raise LLMCtlError(
            "PROVIDER_ERROR", f"{provider_name} response was not an object"
        )

    _write(
        {
            "event": "response",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "payload": _truncate_debug_value(parsed, max_chars),
        }
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
    env_owner = resolve_environment_config(env=env)
    request_headers = with_default_user_agent(headers)

    def _write(event: Dict[str, Any]) -> None:
        _write_llm_debug_event(event, env=env_owner)

    trace_id = uuid.uuid4().hex
    max_chars = _llm_debug_max_chars(env=env_owner)
    _write(
        {
            "event": "request",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "timeout_seconds": timeout_seconds,
            "payload": _truncate_debug_value(payload, max_chars),
        }
    )

    body_json = json.dumps(payload)
    body = body_json.encode("utf-8")

    trace_http_json_request(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        body_json=body_json,
        payload=payload,
        headers=request_headers,
        timeout_seconds=timeout_seconds,
        transport="urllib",
        env=env_owner,
    )
    request_obj = urllib_request.Request(
        url,
        data=body,
        headers=request_headers,
        method="POST",
    )

    try:
        with urllib_request.urlopen(
            request_obj, timeout=float(timeout_seconds)
        ) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            raw = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        detail = _safe_http_error_body(exc)
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
            raise LLMCtlError(
                "TIMEOUT",
                f"{provider_name} request timed out: {reason}",
                details={"provider": provider_name, "url": url},
            ) from exc
        if _should_use_curl_fallback(reason):
            return _curl_json_post(
                url=url,
                payload=payload,
                body_json=body_json,
                headers=headers,
                timeout_seconds=timeout_seconds,
                provider_name=provider_name,
                reason=reason,
                trace_metadata=trace_metadata,
                env=env_owner,
            )
        raise LLMCtlError(
            "PROVIDER_ERROR",
            f"{provider_name} request failed: {reason}",
            details={"provider": provider_name, "url": url},
        ) from exc

    parsed: dict[str, Any] | None = None
    parse_error = ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
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
        raise LLMCtlError(
            "PROVIDER_ERROR", f"{provider_name} response was not an object"
        )

    _write(
        {
            "event": "response",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "payload": _truncate_debug_value(parsed, max_chars),
        }
    )
    return parsed


def _curl_json_post(
    *,
    url: str,
    payload: Dict[str, Any],
    body_json: str | None,
    headers: Dict[str, str],
    timeout_seconds: int,
    provider_name: str,
    reason: str,
    trace_metadata: Dict[str, Any] | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> Dict[str, Any]:
    env_owner = resolve_environment_config(env=env)
    request_headers = with_default_user_agent(headers)

    def _write(event: Dict[str, Any]) -> None:
        _write_llm_debug_event(event, env=env_owner)

    trace_id = uuid.uuid4().hex
    max_chars = _llm_debug_max_chars(env=env_owner)
    _write(
        {
            "event": "request",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "timeout_seconds": timeout_seconds,
            "payload": _truncate_debug_value(payload, max_chars),
            "transport": "curl",
        }
    )

    serialized_body = body_json if body_json is not None else json.dumps(payload)

    trace_http_json_request(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        body_json=serialized_body,
        payload=payload,
        headers=request_headers,
        timeout_seconds=timeout_seconds,
        transport="curl",
        env=env_owner,
    )

    if shutil.which("curl") is None:
        raise LLMCtlError("PROVIDER_ERROR", f"{provider_name} request failed: {reason}")

    args = [
        "curl",
        "-sS",
        "-X",
        "POST",
        url,
        "--max-time",
        str(timeout_seconds),
    ]
    for key, value in request_headers.items():
        args.extend(["-H", f"{key}: {value}"])
    args.extend(["-d", serialized_body, "-w", "\n%{http_code}"])

    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or reason
        _write(
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

    raw = result.stdout
    if "\n" in raw:
        raw_body, raw_code = raw.rsplit("\n", 1)
    else:
        raw_body, raw_code = raw, "0"
    try:
        status_code = int(raw_code.strip() or 0)
    except ValueError:
        status_code = 0

    if status_code in {401, 403}:
        trace_http_json_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            body_text=raw_body,
            transport="curl",
            env=env_owner,
        )
        _write(
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
        raise LLMCtlError(
            "AUTH_ERROR",
            f"{provider_name} auth failed: {raw_body.strip() or '(no response body)'}",
        )
    if status_code == 429:
        trace_http_json_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            body_text=raw_body,
            transport="curl",
            env=env_owner,
        )
        _write(
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
        raise LLMCtlError(
            "RATE_LIMITED",
            f"{provider_name} rate limited: {raw_body.strip() or '(no response body)'}",
        )
    if status_code in {408, 504}:
        trace_http_json_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            body_text=raw_body,
            transport="curl",
            env=env_owner,
        )
        _write(
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
        raise LLMCtlError(
            "TIMEOUT",
            f"{provider_name} timeout: {raw_body.strip() or '(no response body)'}",
        )
    if status_code >= 400:
        trace_http_json_response(
            trace_metadata=trace_metadata,
            provider_name=provider_name,
            url=url,
            status_code=status_code,
            body_text=raw_body,
            transport="curl",
            env=env_owner,
        )
        _write(
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
        raise LLMCtlError(
            "PROVIDER_ERROR",
            f"{provider_name} request failed with HTTP {status_code}: {raw_body.strip() or '(no response body)'}",
        )

    parsed: dict[str, Any] | None = None
    parse_error = ""
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
        _write(
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
    else:
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

    if not isinstance(parsed, dict):
        _write_llm_debug_event(
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
        raise LLMCtlError(
            "PROVIDER_ERROR", f"{provider_name} response was not an object"
        )

    _write(
        {
            "event": "response",
            "provider": provider_name,
            "trace_id": trace_id,
            "url": url,
            "payload": _truncate_debug_value(parsed, max_chars),
            "transport": "curl",
        }
    )
    return parsed
