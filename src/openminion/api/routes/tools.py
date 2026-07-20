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
    query_value,
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


def _exposure_scope(body: dict[str, object] | None) -> dict[str, str]:
    values = body or {}
    return {
        "session_id": str(values.get("session_id", "") or "").strip(),
        "task_id": str(values.get("task_id", "") or "").strip(),
        "target_id": str(values.get("target_id", "") or "").strip(),
    }


def _string_tuple(body: dict, name: str) -> tuple[str, ...]:
    raw = body.get(name, ())
    values = (raw,) if isinstance(raw, str) else tuple(raw or ())
    return tuple(value for item in values if (value := str(item or "").strip()))


def _handle_exposure_status(
    ctx: APIRouteContext,
    *,
    query: str | None,
) -> RouteResult:
    def _build(active_runtime) -> RouteResult:
        return RouteResult(
            status=HTTPStatus.OK,
            payload={
                "ok": True,
                "exposure": active_runtime.tool_exposure_status(
                    session_id=query_value(query, "session_id") or "",
                    task_id=query_value(query, "task_id") or "",
                    target_id=query_value(query, "target_id") or "",
                ),
            },
        )

    return _with_runtime(ctx, _build)


def _handle_exposure_activate(
    ctx: APIRouteContext,
    *,
    path: str,
    body: dict | None,
) -> RouteResult:
    def _build(active_runtime) -> RouteResult:
        if body is None:
            return json_body_required_route_result(path=path)
        profile_id = str(body.get("profile_id", "") or "").strip()
        scope = _exposure_scope(body)
        if not profile_id or not scope["session_id"]:
            return error_route_result(
                HTTPStatus.BAD_REQUEST,
                code="invalid_request",
                message="profile_id and session_id are required",
                details={"path": path},
                retryable=False,
            )
        try:
            activation = active_runtime.activate_tool_profile(
                profile_id,
                **scope,
                target_kind=str(body.get("target_kind", "") or "").strip(),
                credential_scopes=_string_tuple(body, "credential_scopes"),
                dependencies=_string_tuple(body, "dependencies"),
                approved=bool(body.get("approved", False)),
                ttl_seconds=(
                    float(body["ttl_seconds"])
                    if body.get("ttl_seconds") is not None
                    else None
                ),
                activation_reason=str(body.get("activation_reason", "") or "").strip(),
                approved_by=str(body.get("approved_by", "") or "").strip(),
                policy_source=str(body.get("policy_source", "") or "").strip(),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return exception_route_result(
                HTTPStatus.BAD_REQUEST,
                code="tool_exposure_activation_denied",
                exc=exc,
                details={"profile_id": profile_id},
                retryable=False,
            )
        return RouteResult(
            status=HTTPStatus.OK,
            payload={"ok": True, "activation": activation},
        )

    return _with_runtime(ctx, _build)


def _handle_exposure_deactivate(
    ctx: APIRouteContext,
    *,
    path: str,
    body: dict | None,
) -> RouteResult:
    def _build(active_runtime) -> RouteResult:
        if body is None:
            return json_body_required_route_result(path=path)
        profile_id = str(body.get("profile_id", "") or "").strip()
        scope = _exposure_scope(body)
        if not profile_id or not scope["session_id"]:
            return error_route_result(
                HTTPStatus.BAD_REQUEST,
                code="invalid_request",
                message="profile_id and session_id are required",
                details={"path": path},
                retryable=False,
            )
        deactivated = active_runtime.deactivate_tool_profile(profile_id, **scope)
        return RouteResult(
            status=HTTPStatus.OK,
            payload={"ok": True, "deactivated": deactivated},
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
            **request,
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
    if method_name == "GET" and path == "/v1/tools":
        return _handle_list_tools(ctx)

    if method_name == "GET" and path == "/v1/tools/exposure":
        return _handle_exposure_status(ctx, query=query)

    if method_name == "POST" and path == "/v1/tools/exposure/activate":
        return _handle_exposure_activate(ctx, path=path, body=body)

    if method_name == "POST" and path == "/v1/tools/exposure/deactivate":
        return _handle_exposure_deactivate(ctx, path=path, body=body)

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
