"""Local HTTP authentication for the OpenMinion API server."""

import logging
from hmac import compare_digest
from http import HTTPStatus
from ipaddress import ip_address
from typing import Any

from openminion.api.responses.serialization import error_response, normalize_request_id
from openminion.api.server.observability import finalize_api_response


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def require_ipc_token_for_bind(host: str, token: str, runtime: Any) -> None:
    if is_loopback_host(host) or token:
        return
    if runtime is not None:
        runtime.close()
    raise RuntimeError("A non-loopback API bind requires runtime.ipc_token.")


def authorize_ipc_request(
    handler: Any,
    *,
    method: str,
    path: str,
    request_id: str | None,
    started_at: float,
) -> bool:
    expected = str(getattr(handler, "ipc_token", "") or "").strip()
    if not expected:
        return True
    provided = str(handler.headers.get("X-IPC-Token") or "")
    if compare_digest(provided, expected):
        return True
    status, payload = error_response(
        HTTPStatus.UNAUTHORIZED,
        code="ipc_auth_required",
        message="A valid local IPC token is required.",
        details={"path": path},
        retryable=False,
    )
    response = finalize_api_response(
        payload=payload,
        status=status,
        method=method,
        path=path,
        request_id=normalize_request_id(request_id),
        started_at=started_at,
        logger=logging.getLogger("openminion.api"),
    )
    handler._write_json(status, response)
    return False


__all__ = [
    "authorize_ipc_request",
    "is_loopback_host",
    "require_ipc_token_for_bind",
]
