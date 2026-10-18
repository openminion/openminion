import re
from http import HTTPStatus
from urllib.parse import unquote

from openminion.api.operations.tasks import (
    TaskControlOperationError,
    execute_task_control,
)

from .contracts import (
    APIRouteContext,
    RouteResult,
    exception_route_result,
    runtime_unavailable_route_result,
)
from .task_responses import task_agent_id_required

_TASK_RUN_NOW_RE = re.compile(r"/v1/tasks/([^/]+)/run-now")
_TASK_HISTORY_DELETE_RE = re.compile(r"/v1/tasks/([^/]+)/history/delete")
_TASK_SCHEDULER_RE = re.compile(r"/v1/tasks/scheduler(?:/(pause|resume))?")
_TASK_RE = re.compile(r"/v1/tasks/([^/]+)")


def handle_task_control_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, object] | None,
    agent_id: str,
    session_id: str,
) -> RouteResult | None:
    tool_name = ""
    arguments: dict[str, object] = {}
    if method_name == "GET" and _TASK_SCHEDULER_RE.fullmatch(path):
        tool_name = "task.scheduler_status"
    elif method_name == "POST" and (match := _TASK_SCHEDULER_RE.fullmatch(path)):
        action = str(match.group(1) or "")
        if not action:
            return None
        tool_name = f"task.scheduler_{action}"
        arguments = dict(body or {})
        arguments.pop("confirm", None)
    elif method_name == "POST" and (match := _TASK_RUN_NOW_RE.fullmatch(path)):
        tool_name = "task.run_now"
        arguments = {"task_id": unquote(match.group(1))}
    elif method_name == "POST" and (match := _TASK_HISTORY_DELETE_RE.fullmatch(path)):
        tool_name = "task.delete_history"
        arguments = {"task_id": unquote(match.group(1))}
    elif method_name == "PATCH" and (match := _TASK_RE.fullmatch(path)):
        tool_name = "task.update"
        arguments = dict(body or {})
        arguments["task_id"] = unquote(match.group(1))
    else:
        return None

    if ctx.runtime is None:
        return runtime_unavailable_route_result(path=path, exc="Runtime not available.")
    if not agent_id:
        return task_agent_id_required()
    arguments.pop("confirm", None)
    try:
        payload = execute_task_control(
            runtime=ctx.runtime,
            tool_name=tool_name,
            arguments=arguments,
            agent_id=agent_id,
            session_id=session_id,
            request_id=ctx.request_id,
            confirm=(body or {}).get("confirm") is True,
        )
        return RouteResult(status=HTTPStatus.OK, payload=payload)
    except TaskControlOperationError as exc:
        reason = str(exc.details.get("reason_code") or "")
        status = (
            HTTPStatus.FORBIDDEN
            if exc.is_forbidden
            else {
                "record_not_found": HTTPStatus.NOT_FOUND,
                "task_ownership_denied": HTTPStatus.FORBIDDEN,
                "task_not_active": HTTPStatus.CONFLICT,
                "task_busy": HTTPStatus.CONFLICT,
                "task_revision_conflict": HTTPStatus.CONFLICT,
                "scheduler_revision_conflict": HTTPStatus.CONFLICT,
                "scheduler_paused": HTTPStatus.CONFLICT,
                "task_unsupported_kind": HTTPStatus.BAD_REQUEST,
                "task_past_due": HTTPStatus.BAD_REQUEST,
            }.get(reason, HTTPStatus.BAD_REQUEST)
        )
        return exception_route_result(
            status,
            code=exc.code,
            exc=exc,
            details=exc.details,
            retryable=False,
        )
