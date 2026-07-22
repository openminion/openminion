from __future__ import annotations

from http import HTTPStatus
from time import perf_counter as _perf_counter
from typing import Any, Mapping

import openminion.api.server.app as _server_app
import openminion.api.routes.turns as _routes_turns
from openminion.api.config import bootstrap_api_runtime, build_api_handler_class
from openminion.api.server.app import (
    _OpenMinionAPIHandler,
    _OpenMinionThreadingHTTPServer,
    get_api_metrics_consistency_stamp,
    get_api_metrics_snapshot,
    reset_api_metrics,
)
from openminion.api.runtime import APIRuntime
from openminion.api.turns import run_turn
from openminion.api.core.validation import parse_json_request_body

perf_counter = _perf_counter


def build_api_server(
    config_path: str | None,
    host: str,
    port: int,
) -> _OpenMinionThreadingHTTPServer:
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
    config_path: str | None,
    body: dict[str, Any] | None = None,
    query: str | None = None,
    runtime: APIRuntime | None = None,
    runtime_bootstrap_error: str | None = None,
    request_headers: Mapping[str, str] | None = None,
    request_id: str | None = None,
) -> tuple[HTTPStatus, dict[str, Any]]:
    had_run_turn = hasattr(_server_app, "run_turn")
    orig_run_turn = getattr(_server_app, "run_turn", None)
    orig_perf_counter = _server_app.perf_counter
    orig_api_runtime = _server_app.APIRuntime
    orig_routes_run_turn = _routes_turns.run_turn
    try:
        _server_app.run_turn = run_turn
        _server_app.perf_counter = perf_counter
        _server_app.APIRuntime = APIRuntime
        _routes_turns.run_turn = run_turn
        return _server_app.dispatch_request(
            method,
            path,
            config_path,
            body=body,
            query=query,
            runtime=runtime,
            runtime_bootstrap_error=runtime_bootstrap_error,
            request_headers=request_headers,
            request_id=request_id,
        )
    finally:
        if had_run_turn:
            _server_app.run_turn = orig_run_turn
        else:
            try:
                delattr(_server_app, "run_turn")
            except AttributeError:
                pass
        _server_app.perf_counter = orig_perf_counter
        _server_app.APIRuntime = orig_api_runtime
        _routes_turns.run_turn = orig_routes_run_turn


__all__ = [
    "_OpenMinionAPIHandler",
    "_OpenMinionThreadingHTTPServer",
    "APIRuntime",
    "build_api_server",
    "dispatch_request",
    "get_api_metrics_consistency_stamp",
    "get_api_metrics_snapshot",
    "reset_api_metrics",
    "perf_counter",
    "run_turn",
    "parse_json_request_body",
]
