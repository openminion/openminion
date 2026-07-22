"""Stdlib HTTP and SSE transport for the OpenMinion API."""

from __future__ import annotations

import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

from openminion.api.config import bootstrap_api_runtime, build_api_handler_class
from openminion.api.core.validation import parse_json_request_body
from openminion.api.responses.serialization import error_response, normalize_request_id
from openminion.api.runtime import APIRuntime
from openminion.api.server.dispatch import dispatch_request
from openminion.api.server.observability import (
    finalize_api_response as _finalize_api_response,
    get_api_metrics_consistency_stamp,
    get_api_metrics_snapshot,
    log_request_done as _log_request_done,
    observe_request_metrics as _observe_request_metrics,
    reset_api_metrics,
)


class _OpenMinionAPIHandler(BaseHTTPRequestHandler):
    config_path: str | None = None
    runtime: APIRuntime | None = None
    runtime_bootstrap_error: str | None = None

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urlparse(self.path)
        request_id = self.headers.get("X-Request-ID")
        status, payload = dispatch_request(
            "GET",
            parsed.path,
            self.config_path,
            query=parsed.query,
            runtime=self.runtime,
            runtime_bootstrap_error=self.runtime_bootstrap_error,
            request_headers=dict(self.headers.items()),
            request_id=request_id,
        )
        self._write_json(status, payload)

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        path = urlparse(self.path).path
        request_id = self.headers.get("X-Request-ID")
        started_at = perf_counter()
        try:
            payload = self._read_optional_json_body()
        except ValueError as exc:
            self._write_invalid_json("POST", path, request_id, started_at, exc)
            return

        accept_header = str(self.headers.get("Accept", "") or "").lower()
        if path == "/v1/turn/stream" and "text/event-stream" in accept_header:
            self._handle_turn_stream(body=payload, request_id=request_id)
            return

        status, response_payload = dispatch_request(
            "POST",
            path,
            self.config_path,
            body=payload,
            runtime=self.runtime,
            runtime_bootstrap_error=self.runtime_bootstrap_error,
            request_headers=dict(self.headers.items()),
            request_id=request_id,
        )
        self._write_json(status, response_payload)

    def do_DELETE(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urlparse(self.path)
        request_id = self.headers.get("X-Request-ID")
        started_at = perf_counter()
        try:
            payload = self._read_optional_json_body()
        except ValueError as exc:
            self._write_invalid_json("DELETE", parsed.path, request_id, started_at, exc)
            return
        status, response_payload = dispatch_request(
            "DELETE",
            parsed.path,
            self.config_path,
            body=payload,
            query=parsed.query,
            runtime=self.runtime,
            runtime_bootstrap_error=self.runtime_bootstrap_error,
            request_headers=dict(self.headers.items()),
            request_id=request_id,
        )
        self._write_json(status, response_payload)

    def _read_optional_json_body(self) -> dict[str, Any]:
        content_length_raw = self.headers.get("Content-Length", "0")
        try:
            content_length = int(content_length_raw)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header.") from exc
        if content_length <= 0:
            return {}
        raw_body = self.rfile.read(content_length).decode("utf-8")
        return parse_json_request_body(
            content_length_raw=content_length_raw,
            raw_body=raw_body,
        )

    def _handle_turn_stream(self, *, body: dict[str, Any], request_id: str | None) -> None:
        from openminion.api.server.streaming import handle_turn_stream_request

        handle_turn_stream_request(
            body=body,
            request_id=request_id,
            config_path=self.config_path,
            runtime=self.runtime,
            start_sse_response=lambda: _start_sse_stream_response(self, request_id),
            write_sse_event=self._write_sse_event,
            write_json=self._write_json,
            observe_request_metrics=_observe_request_metrics,
            log_request_done=_log_request_done,
            perf_counter=perf_counter,
        )

    def _write_invalid_json(
        self,
        method: str,
        path: str,
        request_id: str | None,
        started_at: float,
        exc: ValueError,
    ) -> None:
        status, payload = error_response(
            HTTPStatus.BAD_REQUEST,
            code="invalid_json",
            message=str(exc),
            details={"path": path},
            retryable=False,
        )
        response = _finalize_api_response(
            payload=payload,
            status=status,
            method=method,
            path=path,
            request_id=normalize_request_id(request_id),
            started_at=started_at,
            logger=logging.getLogger("openminion.api"),
        )
        self._write_json(status, response)

    def _write_sse_event(self, *, event: str, data: object) -> None:
        self.wfile.write(f"event: {event}\ndata: {_json_dumps(data)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        meta = payload.get("meta", {})
        if request_id := meta.get("request_id"):
            self.send_header("X-Request-ID", request_id)
        if (retry_after_ms := payload.get("error", {}).get("retry_after_ms")) is not None:
            self.send_header("Retry-After", str(max(1, int(retry_after_ms) // 1000)))
        if meta.get("path") == "/metrics":
            self.send_header("Cache-Control", "no-store")
        response_headers = meta.get("response_headers")
        if isinstance(response_headers, dict):
            for key, value in response_headers.items():
                if key in {"Cache-Control", "Referrer-Policy"}:
                    self.send_header(str(key), str(value))
        self.end_headers()
        self.wfile.write(_json_dumps(payload).encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        # Keep API tests and CLI output deterministic.
        return


def build_api_server(config_path: str | None, host: str, port: int) -> ThreadingHTTPServer:
    bootstrap = bootstrap_api_runtime(config_path)
    handler_cls = build_api_handler_class(
        _OpenMinionAPIHandler,
        config_path=config_path,
        bootstrap=bootstrap,
    )
    return _OpenMinionThreadingHTTPServer(
        (host, int(port)),
        handler_cls,
        runtime=bootstrap.runtime,
    )


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

    def server_close(self) -> None:
        try:
            if self._runtime is not None:
                self._runtime.close()
        finally:
            super().server_close()


def _start_sse_stream_response(handler: _OpenMinionAPIHandler, request_id: str | None) -> None:
    handler.send_response(int(HTTPStatus.OK))
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("X-Request-ID", normalize_request_id(request_id))
    handler.end_headers()


__all__ = [
    "_OpenMinionAPIHandler",
    "build_api_server",
    "dispatch_request",
    "get_api_metrics_consistency_stamp",
    "get_api_metrics_snapshot",
    "reset_api_metrics",
]
