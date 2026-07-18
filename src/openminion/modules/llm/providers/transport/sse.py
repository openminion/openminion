import socket
from typing import Any, Dict, Iterator, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from openminion.base.config.env import EnvironmentConfig
from ...errors import LLMCtlError
from .http import _safe_http_error_body, with_default_user_agent
from .payload import serialize_json_payload
from .trace import trace_http_json_request


def iter_sse_post_lines(
    *,
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout_seconds: int,
    provider_name: str,
    trace_metadata: Dict[str, Any] | None = None,
    transport: str = "urllib_stream",
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> Iterator[str]:
    """POST JSON, then iterate decoded SSE response lines.

    The caller is responsible for parsing `data:` lines and emitting stream events.
    """
    serialized_payload = serialize_json_payload(payload)
    request_headers = with_default_user_agent(headers)

    trace_http_json_request(
        trace_metadata=trace_metadata,
        provider_name=provider_name,
        url=url,
        body_json=serialized_payload.body_json,
        payload=serialized_payload.payload,
        headers=request_headers,
        timeout_seconds=timeout_seconds,
        transport=transport,
        env=env,
    )

    req_obj = urllib_request.Request(
        url,
        data=serialized_payload.body_bytes,
        headers=request_headers,
        method="POST",
    )

    try:
        with urllib_request.urlopen(
            req_obj, timeout=float(timeout_seconds)
        ) as response:
            for raw_line in response:
                yield raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
    except urllib_error.HTTPError as exc:
        detail = _safe_http_error_body(exc)
        if exc.code in {401, 403}:
            raise LLMCtlError(
                "AUTH_ERROR", f"{provider_name} auth failed: {detail}"
            ) from exc
        if exc.code == 429:
            raise LLMCtlError(
                "RATE_LIMITED", f"{provider_name} rate limited: {detail}"
            ) from exc
        if exc.code in {408, 504}:
            raise LLMCtlError("TIMEOUT", f"{provider_name} timeout: {detail}") from exc
        raise LLMCtlError(
            "PROVIDER_ERROR",
            f"{provider_name} request failed with HTTP {exc.code}: {detail}",
        ) from exc
    except urllib_error.URLError as exc:
        reason = str(exc.reason)
        if isinstance(exc.reason, socket.timeout) or "timed out" in reason.lower():
            raise LLMCtlError(
                "TIMEOUT", f"{provider_name} request timed out: {reason}"
            ) from exc
        raise LLMCtlError(
            "PROVIDER_ERROR", f"{provider_name} request failed: {reason}"
        ) from exc
