"""Stdlib HTTP and SSE transport for the OpenMinion API."""

import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

from openminion.api.responses.serialization import error_response, normalize_request_id
from openminion.api.runtime import APIRuntime
from openminion.api.server import client_approvals, client_artifacts, client_media
from openminion.api.server.client_auth import ClientAuthHTTPMixin
from openminion.api.server.dispatch import dispatch_request
from openminion.api.server.observability import (
    finalize_api_response,
    get_api_metrics_consistency_stamp,
    get_api_metrics_snapshot,
    reset_api_metrics,
)
from openminion.api.server.streaming import (
    handle_http_turn_stream_request,
    try_handle_turn_stream_attach,
    write_sse_event,
)


class _OpenMinionAPIHandler(
    ClientAuthHTTPMixin,  # type: ignore[misc]
    BaseHTTPRequestHandler,
):
    config_path: str | None = None
    runtime: APIRuntime | None = None
    runtime_bootstrap_error: str | None = None

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urlparse(self.path)
        path = parsed.path
        request_id = self.headers.get("X-Request-ID")
        started_at = perf_counter()
        if not self._authorize_request(
            "GET", path, request_id, started_at=started_at, query=parsed.query
        ):
            return
        if self.client_identity is None and try_handle_turn_stream_attach(
            self, parsed=parsed, request_id=request_id
        ):
            return
        status, payload = dispatch_request(
            "GET",
            path,
            self.config_path,
            query=parsed.query,
            runtime=self.runtime,
            runtime_bootstrap_error=self.runtime_bootstrap_error,
            request_headers=dict(self.headers.items()),
            request_id=request_id,
            **self._client_dispatch_context(),
        )
        self._write_json(status, payload)

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urlparse(self.path)
        path = parsed.path
        request_id = self.headers.get("X-Request-ID")
        started_at = perf_counter()
        if not self._authorize_request(
            "POST", path, request_id, started_at=started_at, query=parsed.query
        ):
            return
        try:
            payload = self._read_optional_json_body(path=path)
        except ValueError as exc:
            self._write_invalid_json("POST", path, request_id, started_at, exc)
            return

        if path == "/v1/turn/stream" and self._accepts_event_stream():
            if self.client_identity is None:
                handle_http_turn_stream_request(
                    self, body=payload, request_id=request_id
                )
            else:
                self._handle_client_turn_stream(body=payload, request_id=request_id)
            return

        status, response_payload = dispatch_request(
            "POST",
            path,
            self.config_path,
            body=payload,
            query=parsed.query,
            runtime=self.runtime,
            runtime_bootstrap_error=self.runtime_bootstrap_error,
            request_headers=dict(self.headers.items()),
            request_id=request_id,
            **self._client_dispatch_context(),
        )
        self._write_json(status, response_payload)

    def do_DELETE(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urlparse(self.path)
        path = parsed.path
        request_id = self.headers.get("X-Request-ID")
        started_at = perf_counter()
        if not self._authorize_request(
            "DELETE", path, request_id, started_at=started_at, query=parsed.query
        ):
            return
        try:
            payload = self._read_optional_json_body(path=path)
        except ValueError as exc:
            self._write_invalid_json("DELETE", path, request_id, started_at, exc)
            return
        status, response_payload = dispatch_request(
            "DELETE",
            path,
            self.config_path,
            body=payload,
            query=parsed.query,
            runtime=self.runtime,
            runtime_bootstrap_error=self.runtime_bootstrap_error,
            request_headers=dict(self.headers.items()),
            request_id=request_id,
            **self._client_dispatch_context(),
        )
        self._write_json(status, response_payload)

    def _write_invalid_json(
        self,
        method: str,
        path: str,
        request_id: str | None,
        started_at: float,
        exc: ValueError,
    ) -> None:
        self.close_connection = True
        status, payload = error_response(
            HTTPStatus.BAD_REQUEST,
            code="invalid_json",
            message=str(exc),
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
        self._write_json(status, response)

    def _write_sse_event(
        self,
        *,
        event: str,
        data: object,
        event_id: str | None = None,
    ) -> None:
        write_sse_event(
            self.wfile,
            event=event,
            data=data,
            event_id=event_id,
        )

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        status, encoded = self._bounded_json_response(status, payload)
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        meta = payload.get("meta", {})
        if request_id := meta.get("request_id"):
            self.send_header("X-Request-ID", request_id)
        if (
            retry_after_ms := payload.get("error", {}).get("retry_after_ms")
        ) is not None:
            self.send_header("Retry-After", str(max(1, int(retry_after_ms) // 1000)))
        if meta.get("path") == "/metrics":
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        if getattr(self, "close_connection", False):
            self.send_header("Connection", "close")
        response_headers = meta.get("response_headers")
        if isinstance(response_headers, dict):
            for key, value in response_headers.items():
                if key in {"Cache-Control", "Referrer-Policy"}:
                    self.send_header(str(key), str(value))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def _json_dumps(payload: object) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class _OpenMinionThreadingHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[BaseHTTPRequestHandler],
        runtime: APIRuntime | None,
    ) -> None:
        super().__init__(server_address, handler_cls)
        self._runtime = runtime
        approvals = client_approvals.install_approvals(handler_cls, runtime)
        try:
            media = client_media.install_media(handler_cls, runtime)
            artifacts = client_artifacts.install_artifacts(handler_cls, runtime)
            self._client_state = approvals, media, artifacts
        except Exception:
            client_media.close_media(locals().get("media"))
            client_approvals.close_approvals(approvals)
            raise

    def server_close(self) -> None:
        try:
            client_artifacts.close_artifacts(self._client_state[2])
            client_media.close_media(self._client_state[1])
            client_approvals.close_approvals(self._client_state[0])
            if self._runtime is not None:
                self._runtime.close()
        finally:
            super().server_close()


__all__ = [
    "_OpenMinionAPIHandler", "dispatch_request",
    "get_api_metrics_consistency_stamp", "get_api_metrics_snapshot",
    "reset_api_metrics",
]  # fmt: skip
