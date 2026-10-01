"""Task route handlers for the developer API."""

import re
from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import parse_qs, unquote

from openminion.api.operations.tasks import (
    apply_task_action,
    create_task,
)
from openminion.api.queries.tasks import list_tasks, show_task

from .contracts import (
    APIRouteContext,
    RouteResult,
    exception_route_result,
    runtime_unavailable_route_result,
)
from .task_responses import task_runtime_error, task_scope_denied

_TASKS_RE = re.compile(r"/v1/tasks")
_TASK_ACTION_RE = re.compile(r"/v1/tasks/([^/]+)/(pause|resume|cancel)")
_TASK_RE = re.compile(r"/v1/tasks/([^/]+)")


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
    if method_name == "POST" and _TASKS_RE.fullmatch(path):
        return _create_task(ctx, path=path, body=body, query=query)
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
    return None


def _create_task(
    ctx: APIRouteContext,
    *,
    path: str,
    body: dict[str, object] | None,
    query: str | None,
) -> RouteResult:
    if ctx.runtime is None:
        return runtime_unavailable_route_result(path=path, exc="Runtime not available.")
    options = _query_options(query)
    if not options.agent_id:
        return _agent_id_required()
    try:
        payload = create_task(
            runtime=ctx.runtime,
            body=body,
            agent_id=options.agent_id,
            session_id=options.session_id,
        )
    except ValueError as exc:
        return exception_route_result(
            HTTPStatus.BAD_REQUEST,
            code="invalid_task",
            exc=exc,
            details={},
            retryable=False,
        )
    except (AttributeError, TypeError, RuntimeError) as exc:
        return task_runtime_error(exc)
    return RouteResult(
        status=HTTPStatus.OK if payload["deduped"] else HTTPStatus.CREATED,
        payload=payload,
    )


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
    options = _query_options(query)
    if not options.agent_id:
        return _agent_id_required()
    try:
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
        return task_runtime_error(exc)


def _show_task(
    ctx: APIRouteContext, *, task_id: str, path: str, query: str | None
) -> RouteResult:
    if ctx.runtime is None:
        return runtime_unavailable_route_result(path=path, exc="Runtime not available.")
    options = _query_options(query)
    if not options.agent_id:
        return _agent_id_required()
    try:
        task = show_task(
            runtime=ctx.runtime,
            task_id=task_id,
            agent_id=options.agent_id,
            session_id=options.session_id,
            limit=options.limit,
        )
    except PermissionError as exc:
        return task_scope_denied(exc, task_id=task_id)
    except (AttributeError, TypeError, RuntimeError) as exc:
        return task_runtime_error(exc)
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
    options = _query_options(query)
    if not options.agent_id:
        return _agent_id_required()
    try:
        payload = apply_task_action(
            runtime=ctx.runtime,
            task_id=task_id,
            action=action,
            agent_id=options.agent_id,
            session_id=options.session_id,
            limit=options.limit,
        )
        return RouteResult(status=HTTPStatus.OK, payload=payload)
    except PermissionError as exc:
        return task_scope_denied(exc, task_id=task_id)
    except KeyError as exc:
        return exception_route_result(
            HTTPStatus.NOT_FOUND,
            code="task_not_found",
            exc=exc,
            details={"task_id": task_id},
            retryable=False,
        )
    except (ValueError, NotImplementedError) as exc:
        code = str(getattr(exc, "code", "") or "invalid_task_action")
        typed_details = getattr(exc, "details", None)
        return exception_route_result(
            HTTPStatus.BAD_REQUEST,
            code=code,
            exc=exc,
            details=(
                dict(typed_details)
                if isinstance(typed_details, dict)
                else {"task_id": task_id, "action": action}
            ),
            retryable=False,
        )
    except (AttributeError, TypeError, RuntimeError) as exc:
        return task_runtime_error(exc)


def _first_query_value(params: dict[str, list[str]], key: str) -> str:
    return (params.get(key) or [""])[0].strip()


def _agent_id_required() -> RouteResult:
    return exception_route_result(
        HTTPStatus.BAD_REQUEST,
        code="agent_id_required",
        exc=ValueError("agent_id query parameter is required"),
        details={},
        retryable=False,
    )


def _safe_int(value: str, *, default: int) -> int:
    try:
        return max(1, int(value))
    except ValueError:
        return default
