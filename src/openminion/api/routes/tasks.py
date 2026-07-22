"""Task route handlers for the developer API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import parse_qs, unquote

from openminion.api.operations.tasks import apply_pending_action, apply_task_action
from openminion.api.queries.tasks import list_tasks, show_task

from .contracts import (
    APIRouteContext,
    RouteResult,
    exception_route_result,
    runtime_unavailable_route_result,
)

_TASKS_RE = re.compile(r"/v1/tasks")
_TASK_ACTION_RE = re.compile(r"/v1/tasks/([^/]+)/(pause|resume|cancel)")
_TASK_RE = re.compile(r"/v1/tasks/([^/]+)")
_PENDING_ACTION_RE = re.compile(r"/v1/tasks/pending/([^/]+)/(allow|deny)")


@dataclass(frozen=True)
class _TaskRouteOptions:
    agent_id: str
    session_id: str
    limit: int


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, object] | None,
    query: str | None,
) -> RouteResult | None:
    del body
    if method_name == "GET" and _TASKS_RE.fullmatch(path):
        return _list_tasks(ctx, path=path, query=query)
    if method_name == "GET" and (m := _TASK_RE.fullmatch(path)):
        return _show_task(ctx, task_id=unquote(m.group(1)), path=path, query=query)
    if method_name == "POST" and (m := _TASK_ACTION_RE.fullmatch(path)):
        return _apply_task_action(
            ctx,
            task_id=unquote(m.group(1)),
            action=m.group(2),
            path=path,
            query=query,
        )
    if method_name == "POST" and (m := _PENDING_ACTION_RE.fullmatch(path)):
        return _apply_pending_action(
            ctx,
            decision_id=unquote(m.group(1)),
            action=m.group(2),
            path=path,
            query=query,
        )
    return None


def _query_options(query: str | None) -> _TaskRouteOptions:
    params = parse_qs(query or "")
    return _TaskRouteOptions(
        agent_id=_first_query_value(params, "agent_id"),
        session_id=_first_query_value(params, "session_id"),
        limit=_safe_int(_first_query_value(params, "limit"), default=50),
    )


def _list_tasks(ctx: APIRouteContext, *, path: str, query: str | None) -> RouteResult:
    if ctx.runtime is None:
        return runtime_unavailable_route_result(path=path, exc="Runtime not available.")
    try:
        options = _query_options(query)
        return RouteResult(
            status=HTTPStatus.OK,
            payload=list_tasks(
                runtime=ctx.runtime,
                agent_id=options.agent_id,
                session_id=options.session_id,
                limit=options.limit,
            ),
        )
    except (AttributeError, TypeError, RuntimeError) as exc:
        return _task_error(exc)


def _show_task(
    ctx: APIRouteContext, *, task_id: str, path: str, query: str | None
) -> RouteResult:
    if ctx.runtime is None:
        return runtime_unavailable_route_result(path=path, exc="Runtime not available.")
    try:
        options = _query_options(query)
        task = show_task(
            runtime=ctx.runtime,
            task_id=task_id,
            agent_id=options.agent_id,
            session_id=options.session_id,
            limit=options.limit,
        )
    except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
        return _task_error(exc)
    if task is None:
        return exception_route_result(
            HTTPStatus.NOT_FOUND,
            code="task_not_found",
            exc=KeyError(f"task not found: {task_id}"),
            details={"task_id": task_id},
            retryable=False,
        )
    return RouteResult(status=HTTPStatus.OK, payload={"ok": True, "task": task})


def _apply_task_action(
    ctx: APIRouteContext,
    *,
    task_id: str,
    action: str,
    path: str,
    query: str | None,
) -> RouteResult:
    if ctx.runtime is None:
        return runtime_unavailable_route_result(path=path, exc="Runtime not available.")
    try:
        options = _query_options(query)
        payload = apply_task_action(
            runtime=ctx.runtime,
            task_id=task_id,
            action=action,
            agent_id=options.agent_id,
            session_id=options.session_id,
            limit=options.limit,
        )
        return RouteResult(status=HTTPStatus.OK, payload=payload)
    except KeyError as exc:
        return exception_route_result(
            HTTPStatus.NOT_FOUND,
            code="task_not_found",
            exc=exc,
            details={"task_id": task_id},
            retryable=False,
        )
    except (ValueError, NotImplementedError) as exc:
        return exception_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_task_action",
            exc=exc,
            details={"task_id": task_id, "action": action},
            retryable=False,
        )
    except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
        return _task_error(exc)


def _apply_pending_action(
    ctx: APIRouteContext,
    *,
    decision_id: str,
    action: str,
    path: str,
    query: str | None,
) -> RouteResult:
    if ctx.runtime is None:
        return runtime_unavailable_route_result(path=path, exc="Runtime not available.")
    try:
        options = _query_options(query)
        payload = apply_pending_action(
            runtime=ctx.runtime,
            decision_id=decision_id,
            action=action,
            agent_id=options.agent_id,
            session_id=options.session_id,
            limit=options.limit,
        )
        return RouteResult(status=HTTPStatus.OK, payload=payload)
    except (ValueError, NotImplementedError) as exc:
        return exception_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_task_action",
            exc=exc,
            details={"decision_id": decision_id, "action": action},
            retryable=False,
        )
    except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
        return _task_error(exc)


def _task_error(exc: Exception) -> RouteResult:
    return exception_route_result(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        code="task_error",
        exc=exc,
        details={},
        retryable=False,
    )


def _first_query_value(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key) or []
    return str(values[0] if values else "").strip()


def _safe_int(value: str, *, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default
