"""HTTP server, dispatch, metrics, and response plumbing."""

from __future__ import annotations

import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import perf_counter
from typing import Mapping, Optional
from urllib.parse import urlparse

from openminion.api import metrics_registry
from openminion.api.config import (
    bootstrap_api_runtime,
    build_api_handler_class,
)
from openminion.api.core.validation import parse_json_request_body
from openminion.api.runtime import APIRuntime
from openminion.api.responses.serialization import (
    attach_response_meta,
    error_response,
    normalize_request_id,
    response_error_code,
)
from openminion.api.routes import (
    APIRouteContext,
    RouteResult,
    handle_admin_request,
    handle_agent_request,
    handle_cron_request,
    handle_debug_request,
    handle_health_request,
    handle_memory_request,
    handle_runtime_request,
    handle_sessions_request,
    handle_skill_request,
    handle_tools_request,
    handle_turns_request,
)


_SLOW_REQUEST_WARN_MS = 1000


class _OpenMinionAPIHandler(BaseHTTPRequestHandler):
    config_path: Optional[str] = None
    runtime: Optional[APIRuntime] = None
    runtime_bootstrap_error: Optional[str] = None

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
            request_headers=self.headers,
            request_id=request_id,
        )
        self._write_json(status, payload)

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        path = urlparse(self.path).path
        request_id = self.headers.get("X-Request-ID")
        started_at = perf_counter()
        logger = logging.getLogger("openminion.api")
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            resolved_request_id = normalize_request_id(request_id)
            status, body = error_response(
                HTTPStatus.BAD_REQUEST,
                code="invalid_json",
                message=str(exc),
                details={"path": path},
                retryable=False,
            )
            body = _finalize_api_response(
                payload=body,
                status=status,
                method="POST",
                path=path,
                request_id=resolved_request_id,
                started_at=started_at,
                logger=logger,
            )
            self._write_json(status, body)
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
            request_headers=self.headers,
            request_id=request_id,
        )
        self._write_json(status, response_payload)

    def _handle_turn_stream(self, *, body: dict, request_id: Optional[str]) -> None:
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

    def _write_sse_event(self, *, event: str, data: object) -> None:
        payload = f"event: {event}\ndata: {_json_dumps(data)}\n\n"
        self.wfile.write(payload.encode("utf-8"))
        self.wfile.flush()

    def _write_json(self, status: HTTPStatus, payload: dict) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        request_id = payload.get("meta", {}).get("request_id")
        if request_id:
            self.send_header("X-Request-ID", request_id)
        if payload.get("meta", {}).get("path") == "/metrics":
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        encoded = _json_dumps(payload).encode("utf-8")
        self.wfile.write(encoded)

    def _read_json_body(self) -> dict:
        content_length_raw = self.headers.get("Content-Length", "0")
        try:
            content_length = int(content_length_raw)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header.") from exc
        raw_body = self.rfile.read(max(0, content_length)).decode("utf-8")
        return parse_json_request_body(
            content_length_raw=content_length_raw, raw_body=raw_body
        )

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        # Keep API tests and CLI output deterministic.
        return


def build_api_server(
    config_path: Optional[str], host: str, port: int
) -> ThreadingHTTPServer:
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


def dispatch_request(
    method: str,
    path: str,
    config_path: Optional[str],
    body: Optional[dict] = None,
    query: Optional[str] = None,
    runtime: Optional[APIRuntime] = None,
    runtime_bootstrap_error: Optional[str] = None,
    request_headers: Optional[Mapping[str, str]] = None,
    request_id: Optional[str] = None,
) -> tuple[HTTPStatus, dict]:
    method_name = method.upper().strip() or "GET"
    resolved_request_id = normalize_request_id(request_id)
    started_at = perf_counter()
    logger = logging.getLogger("openminion.api")
    logger.info(
        "api request start method=%s path=%s request_id=%s",
        method_name,
        path,
        resolved_request_id,
    )

    status: HTTPStatus
    payload: dict
    session_id_for_meta: Optional[str] = None
    run_id_for_meta: Optional[str] = None

    ctx = APIRouteContext(
        config_path=config_path,
        runtime=runtime,
        runtime_bootstrap_error=runtime_bootstrap_error,
        request_headers=request_headers,
        request_id=resolved_request_id,
    )

    route_result = handle_health_request(
        ctx,
        method_name=method_name,
        path=path,
        body=body,
        query=query,
    )
    if route_result is None:
        route_result = handle_runtime_request(
            ctx,
            method_name=method_name,
            path=path,
            body=body,
            query=query,
        )

    if route_result is None and runtime_bootstrap_error:
        route_result = _error_route_result(
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="runtime_unavailable",
            message=(
                "API runtime is in degraded mode because startup bootstrap failed. "
                "Use GET /health for details."
            ),
            details={
                "path": path,
                "bootstrap_error": runtime_bootstrap_error,
                "recovery_path": "/health",
                "recommendation": "Check `degraded_recovery` in GET /health and run `openminion doctor --json`.",
            },
            retryable=True,
            retry_after_ms=1000,
        )

    if route_result is None:
        for handler in (
            handle_agent_request,
            handle_tools_request,
            handle_cron_request,
            handle_debug_request,
            handle_turns_request,
            handle_sessions_request,
            handle_memory_request,
            handle_skill_request,
            handle_admin_request,
        ):
            route_result = handler(
                ctx,
                method_name=method_name,
                path=path,
                body=body,
                query=query,
            )
            if route_result is not None:
                break

    if route_result is None:
        route_result = _error_route_result(
            HTTPStatus.NOT_FOUND,
            code="not_found",
            message=f"Unknown path: {path}",
            details={"path": path},
            retryable=False,
        )
    status = route_result.status
    payload = route_result.payload
    session_id_for_meta = route_result.session_id
    run_id_for_meta = route_result.run_id

    response = _finalize_api_response(
        payload=payload,
        status=status,
        method=method_name,
        path=path,
        request_id=resolved_request_id,
        started_at=started_at,
        logger=logger,
        session_id=session_id_for_meta,
        run_id=run_id_for_meta,
    )
    return status, response


def _error_route_result(
    status: HTTPStatus,
    *,
    code: str,
    message: str,
    details: dict,
    retryable: bool,
    retry_after_ms: int | None = None,
) -> RouteResult:
    resolved_status, payload = error_response(
        status,
        code=code,
        message=message,
        details=details,
        retryable=retryable,
        retry_after_ms=retry_after_ms,
    )
    return RouteResult(status=resolved_status, payload=payload)


def _finalize_api_response(
    *,
    payload: dict,
    status: HTTPStatus,
    method: str,
    path: str,
    request_id: str,
    started_at: float,
    logger: logging.Logger,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict:
    response = attach_response_meta(
        payload,
        request_id=request_id,
        method=method,
        path=path,
        session_id=session_id,
        run_id=run_id,
    )
    duration_ms = _observe_request_metrics(
        method=method,
        path=path,
        status=status,
        payload=payload,
        started_at=started_at,
    )
    _log_request_done(
        logger=logger,
        method=method,
        path=path,
        status=status,
        request_id=request_id,
        duration_ms=duration_ms,
        session_id=session_id,
        run_id=run_id,
    )
    return response


def get_api_metrics_snapshot(*, reset: bool = False) -> dict:
    return metrics_registry.snapshot(reset=reset)


def get_api_metrics_consistency_stamp() -> dict:
    return metrics_registry.consistency_stamp()


def reset_api_metrics() -> None:
    metrics_registry.reset()


def _observe_request_metrics(
    *,
    method: str,
    path: str,
    status: HTTPStatus,
    payload: Optional[dict],
    started_at: float,
) -> int:
    duration_ms = max(0, int((perf_counter() - started_at) * 1000))
    route = _route_metric_key(method=method, path=path)
    error_code = response_error_code(payload)
    if route != "GET /metrics":
        metrics_registry.observe(
            route=route,
            status_code=int(status),
            duration_ms=duration_ms,
            error_code=error_code,
        )
    return duration_ms


def _log_request_done(
    *,
    logger: logging.Logger,
    method: str,
    path: str,
    status: HTTPStatus,
    request_id: str,
    duration_ms: int,
    session_id: Optional[str],
    run_id: Optional[str],
) -> None:
    route = _route_metric_key(method=method, path=path)
    status_class = _status_class_label(status)
    logger.info(
        "api request done method=%s path=%s route=%s status=%s status_class=%s request_id=%s session_id=%s run_id=%s duration_ms=%s",
        method,
        path,
        route,
        int(status),
        status_class,
        request_id,
        session_id or "",
        run_id or "",
        duration_ms,
    )
    if duration_ms >= _SLOW_REQUEST_WARN_MS:
        logger.warning(
            "api slow request method=%s path=%s route=%s status=%s status_class=%s request_id=%s session_id=%s run_id=%s duration_ms=%s threshold_ms=%s",
            method,
            path,
            route,
            int(status),
            status_class,
            request_id,
            session_id or "",
            run_id or "",
            duration_ms,
            _SLOW_REQUEST_WARN_MS,
        )


def _status_class_label(status: HTTPStatus) -> str:
    code = int(status)
    return f"{code // 100}xx"


def _route_metric_key(*, method: str, path: str) -> str:
    method_name = method.upper().strip() or "GET"
    if method_name == "GET" and path == "/health":
        return "GET /health"
    if method_name == "GET" and path == "/v1/health":
        return "GET /v1/health"
    if method_name == "GET" and path == "/metrics":
        return "GET /metrics"
    if method_name == "GET" and path == "/v1/agents":
        return "GET /v1/agents"
    if method_name == "GET" and path == "/v1/tools":
        return "GET /v1/tools"
    if method_name == "GET" and re.fullmatch(r"/v1/tools/([^/]+)/schema", path):
        return "GET /v1/tools/{tool}/schema"
    if method_name == "GET" and path == "/owner/status":
        return "GET /owner/status"
    if method_name == "POST" and re.fullmatch(r"/v1/tools/([^/]+)/run", path):
        return "POST /v1/tools/{tool}/run"
    if method_name == "POST" and path == "/v1/turn":
        return "POST /v1/turn"
    if method_name == "POST" and path == "/v1/turn/stream":
        return "POST /v1/turn/stream"
    if method_name == "POST" and re.fullmatch(r"/v1/turn/([^/]+)/cancel", path):
        return "POST /v1/turn/{trace_id}/cancel"
    if method_name == "POST" and re.fullmatch(r"/v1/agents/([^/]+)/evict", path):
        return "POST /v1/agents/{id}/evict"
    if method_name == "GET" and re.fullmatch(r"/v1/agents/([^/]+)/inspect", path):
        return "GET /v1/agents/{id}/inspect"
    if method_name == "GET" and path == "/v1/cron/jobs":
        return "GET /v1/cron/jobs"
    if method_name == "POST" and path == "/v1/cron/jobs":
        return "POST /v1/cron/jobs"
    if method_name == "POST" and re.fullmatch(r"/v1/cron/jobs/([^/]+)/trigger", path):
        return "POST /v1/cron/jobs/{id}/trigger"
    if method_name == "DELETE" and re.fullmatch(r"/v1/cron/jobs/([^/]+)", path):
        return "DELETE /v1/cron/jobs/{id}"
    if method_name == "POST" and path == "/v1/admin/kill":
        return "POST /v1/admin/kill"
    if method_name == "POST" and path == "/turns":
        return "POST /turns"
    if method_name == "GET" and re.fullmatch(r"/sessions/([^/]+)/runs", path):
        return "GET /sessions/{id}/runs"
    if method_name == "GET" and re.fullmatch(
        r"/sessions/([^/]+)/runs/([^/]+)/events", path
    ):
        return "GET /sessions/{id}/runs/{run_id}/events"
    if method_name == "GET" and re.fullmatch(r"/sessions/([^/]+)/messages", path):
        return "GET /sessions/{id}/messages"
    return f"{method_name} /<unknown>"


def _json_dumps(payload: object) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class _OpenMinionThreadingHTTPServer(ThreadingHTTPServer):
    def __init__(
        self, server_address, handler_cls, runtime: Optional[APIRuntime]
    ) -> None:
        super().__init__(server_address, handler_cls)
        self._runtime = runtime

    def server_close(self) -> None:
        try:
            if self._runtime is not None:
                self._runtime.close()
        finally:
            super().server_close()


def _start_sse_stream_response(
    handler: _OpenMinionAPIHandler, request_id: Optional[str]
) -> None:
    handler.send_response(int(HTTPStatus.OK))
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("X-Request-ID", normalize_request_id(request_id))
    handler.end_headers()
