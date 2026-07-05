"""Tool route handlers for schema lookup and execution."""

from __future__ import annotations

import re
from http import HTTPStatus
from urllib.parse import unquote

from openminion.api.core.deps import (
    resolve_runtime_manager,
    v1_tool_schema,
    v1_tool_specs,
)
from openminion.api.core.validation import v1_tool_arguments
from openminion.api.operations.tools import (
    execute_tool_run,
    normalize_tool_run_request,
)

from .contracts import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    exception_route_result,
    json_body_required_route_result,
)


_TOOL_SCHEMA_RE = re.compile(r"/v1/tools/([^/]+)/schema")
_TOOL_RUN_RE = re.compile(r"/v1/tools/([^/]+)/run")


def _with_runtime(
    ctx: APIRouteContext,
    fn,
) -> RouteResult:
    _, active_runtime, own_runtime = resolve_runtime_manager(
        config_path=ctx.config_path,
        runtime=ctx.runtime,
    )
    try:
        return fn(active_runtime)
    finally:
        if own_runtime:
            active_runtime.close()


def _handle_list_tools(ctx: APIRouteContext) -> RouteResult:
    def _build(active_runtime) -> RouteResult:
        return RouteResult(
            status=HTTPStatus.OK,
            payload={"ok": True, "tools": v1_tool_specs(active_runtime)},
        )

    return _with_runtime(ctx, _build)


def _handle_tool_schema(ctx: APIRouteContext, *, tool_name: str) -> RouteResult:
    def _build(active_runtime) -> RouteResult:
        schema = v1_tool_schema(active_runtime, tool_name=tool_name)
        if schema is None:
            return error_route_result(
                HTTPStatus.NOT_FOUND,
                code="tool_not_found",
                message=f"Unknown tool: {tool_name}",
                details={"tool": tool_name},
                retryable=False,
            )
        return RouteResult(status=HTTPStatus.OK, payload={"ok": True, "tool": schema})

    return _with_runtime(ctx, _build)


def _handle_tool_run(
    ctx: APIRouteContext,
    *,
    path: str,
    tool_name: str,
    body: dict | None,
) -> RouteResult:
    def _build(active_runtime) -> RouteResult:
        if body is None:
            return json_body_required_route_result(path=path)
        schema = v1_tool_schema(active_runtime, tool_name=tool_name)
        if schema is None:
            return error_route_result(
                HTTPStatus.NOT_FOUND,
                code="tool_not_found",
                message=f"Unknown tool: {tool_name}",
                details={"tool": tool_name},
                retryable=False,
            )
        try:
            arguments = v1_tool_arguments(body)
        except ValueError as exc:
            return exception_route_result(
                HTTPStatus.BAD_REQUEST,
                code="invalid_request",
                exc=exc,
                details={"path": path},
                retryable=False,
            )
        request = normalize_tool_run_request(body)
        status, payload, session_id = execute_tool_run(
            runtime=active_runtime,
            tool_name=tool_name,
            arguments=arguments,
            request_id=ctx.request_id,
            channel=request["channel"],
            target=request["target"],
            requested_session_id=request["requested_session_id"],
        )
        return RouteResult(
            status=status,
            payload=payload,
            session_id=session_id,
            run_id=ctx.request_id,
        )

    return _with_runtime(ctx, _build)


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict | None,
    query: str | None,
) -> RouteResult | None:
    del query
    if method_name == "GET" and path == "/v1/tools":
        return _handle_list_tools(ctx)

    if (
        method_name == "GET"
        and (tool_schema_route := _TOOL_SCHEMA_RE.fullmatch(path)) is not None
    ):
        return _handle_tool_schema(
            ctx,
            tool_name=unquote(tool_schema_route.group(1)),
        )

    if (
        method_name == "POST"
        and (tool_run_route := _TOOL_RUN_RE.fullmatch(path)) is not None
    ):
        return _handle_tool_run(
            ctx,
            path=path,
            tool_name=unquote(tool_run_route.group(1)),
            body=body,
        )

    return None
