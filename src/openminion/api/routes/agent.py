"""Agent-route handlers for the developer API."""

from __future__ import annotations

import re
from http import HTTPStatus
from urllib.parse import unquote

from openminion.api.operations.agent import evict_agent_runtime
from openminion.api.queries.agents import AgentQueryError, inspect_agent, list_agents

from .base import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    exception_route_result,
    runtime_unavailable_route_result,
)


_INSPECT_RE = re.compile(r"/v1/agents/([^/]+)/inspect")
_EVICT_RE = re.compile(r"/v1/agents/([^/]+)/evict")


def _handle_list_agents(ctx: APIRouteContext, *, path: str) -> RouteResult:
    try:
        payload = list_agents(
            config_path=ctx.config_path,
            runtime=ctx.runtime,
        )
    except Exception as exc:  # noqa: BLE001
        return runtime_unavailable_route_result(path=path, exc=exc)
    return RouteResult(status=HTTPStatus.OK, payload=payload)


def _handle_inspect_agent(
    ctx: APIRouteContext,
    *,
    agent_id: str,
) -> RouteResult:
    try:
        payload = inspect_agent(
            config_path=ctx.config_path,
            runtime=ctx.runtime,
            agent_id=agent_id,
        )
    except AgentQueryError as exc:
        return error_route_result(
            exc.status,
            code=exc.code,
            message=str(exc),
            details={"agent_id": agent_id},
            retryable=False,
        )
    except Exception as exc:  # noqa: BLE001
        return exception_route_result(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="inspect_error",
            exc=exc,
            details={"agent_id": agent_id},
            retryable=False,
        )
    return RouteResult(status=HTTPStatus.OK, payload=payload)


def _handle_evict_agent(
    ctx: APIRouteContext,
    *,
    path: str,
    agent_id: str,
    body: dict | None,
) -> RouteResult:
    reason = "admin_request"
    if body is not None and isinstance(body.get("reason"), str):
        reason = str(body.get("reason")).strip() or reason
    try:
        payload = evict_agent_runtime(
            config_path=ctx.config_path,
            runtime=ctx.runtime,
            agent_id=agent_id,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001
        return runtime_unavailable_route_result(path=path, exc=exc)
    return RouteResult(status=HTTPStatus.OK, payload=payload)


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict | None,
    query: str | None,
) -> RouteResult | None:
    del query
    if method_name == "GET" and path == "/v1/agents":
        return _handle_list_agents(ctx, path=path)

    if (
        method_name == "GET"
        and (inspect_route := _INSPECT_RE.fullmatch(path)) is not None
    ):
        agent_id = unquote(inspect_route.group(1))
        return _handle_inspect_agent(ctx, agent_id=agent_id)

    if method_name == "POST" and (evict_route := _EVICT_RE.fullmatch(path)) is not None:
        agent_id = unquote(evict_route.group(1))
        return _handle_evict_agent(ctx, path=path, agent_id=agent_id, body=body)

    return None
