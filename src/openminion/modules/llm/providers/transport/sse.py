import socket
from collections.abc import Iterator, Mapping
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from openminion.base.config.env import EnvironmentConfig
from ...errors import LLMCtlError
from .client import ProviderHTTPClient
from .error_facts import openai_error_facts, openai_error_message
from .http import _safe_http_error_body, response_request_id, with_default_user_agent
from .payload import serialize_json_payload
from .trace import trace_http_json_request


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
) -> Iterator[str]:
    """POST JSON, then iterate decoded SSE response lines.

    The caller is responsible for parsing `data:` lines and emitting stream events.
    """
    serialized_payload = serialize_json_payload(payload)
    request_headers = with_default_user_agent(headers)
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
        transport=http_client.transport_name if http_client else transport,
        env=env,
    )

    req_obj = urllib_request.Request(
        url,
        data=serialized_payload.body_bytes,
        headers=request_headers,
        method="POST",
    )

    try:
        open_url = http_client.urlopen if http_client else urllib_request.urlopen
        with open_url(req_obj, timeout=float(timeout_seconds)) as response:
            request_id = response_request_id(getattr(response, "headers", None))
            if response_metadata is not None and request_id:
                response_metadata["request_id"] = request_id
            for raw_line in response:
                yield raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
    except urllib_error.HTTPError as exc:
        detail = _safe_http_error_body(exc)
        facts = openai_error_facts(
            detail,
            status_code=int(exc.code),
            request_id=str((exc.headers or {}).get("X-Request-ID") or ""),
        )
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
    except urllib_error.URLError as exc:
        reason = str(exc.reason)
        if isinstance(exc.reason, socket.timeout) or "timed out" in reason.lower():
            raise LLMCtlError(
                "TIMEOUT", f"{provider_name} request timed out: {reason}"
            ) from exc
        raise LLMCtlError(
            "PROVIDER_ERROR", f"{provider_name} request failed: {reason}"
        ) from exc
