from __future__ import annotations

from http import HTTPStatus
from typing import Any, Mapping

from openminion.api.routes.contracts import (
    APIRouteContext,
    RouteResult,
    error_route_result,
)
from openminion.base.config.env import resolve_environment_config
from openminion.modules.a2a.errors import A2AError, ERROR_CODE_JOB_NOT_FOUND
from openminion.modules.a2a import A2ARuntime
from openminion.modules.a2a.endpoint import (
    EXTERNAL_A2A_RUNTIME_ATTR,
    resolve_external_a2a_runtime,
)
from openminion.modules.a2a.models import Envelope, JobRecord, new_uuid
from openminion.modules.a2a.wire.google_a2a_v1.agent_card import (
    AGENT_CARD_WELL_KNOWN_PATH,
    AgentCapabilities,
    AgentSkill,
    build_agent_card,
)
from openminion.modules.a2a.wire.google_a2a_v1.jsonrpc import (
    JsonRpcError,
    JsonRpcErrorCode,
    JsonRpcResponse,
)
from openminion.modules.a2a.wire.google_a2a_v1.task import (
    Task,
    TaskMessage,
    TaskPart,
    TaskState,
)

A2A_NETWORK_TOKEN_ENV = "OPENMINION_A2A_BEARER_TOKEN"


def build_agent_card_payload() -> dict[str, Any]:
    card = build_agent_card(
        name="OpenMinion",
        description="OpenMinion external A2A endpoint for bounded task submission.",
        url="/a2a/v1/jsonrpc",
        version="v1",
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            state_transition_history=True,
            long_running_tasks=True,
        ),
        skills=[
            AgentSkill(
                id="openminion.task",
                name="OpenMinion task execution",
                description="Submit, inspect, and cancel bounded OpenMinion A2A tasks.",
                tags=["openminion", "task", "a2a"],
                examples=["tasks/send", "tasks/get", "tasks/cancel"],
            )
        ],
        documentation_url="https://www.openminion.com/docs/a2a",
    )
    return {
        "agentCard": card.to_jsonable(),
        "auth": {"type": "bearer", "required": True},
    }


def authorize_a2a_request(headers: Mapping[str, str] | None) -> RouteResult | None:
    expected = resolve_environment_config().get(A2A_NETWORK_TOKEN_ENV, "").strip()
    if not expected:
        return error_route_result(
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="a2a_auth_not_configured",
            message="External A2A endpoint requires OPENMINION_A2A_BEARER_TOKEN.",
            details={"token_env": A2A_NETWORK_TOKEN_ENV},
            retryable=False,
        )
    if _bearer_token_from_headers(headers) == expected:
        return None
    return error_route_result(
        HTTPStatus.UNAUTHORIZED,
        code="a2a_auth_required",
        message="External A2A endpoint requires a valid bearer token.",
        details={"auth_scheme": "bearer"},
        retryable=False,
    )


def handle_jsonrpc(ctx: APIRouteContext, body: dict[str, Any]) -> RouteResult:
    try:
        request_id = body.get("id")
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        result = dispatch_jsonrpc_method(
            _resolve_a2a_runtime(ctx),
            method=str(body.get("method", "")),
            params=params,
        )
        return RouteResult(
            HTTPStatus.OK, JsonRpcResponse(id=request_id, result=result).to_jsonable()
        )
    except ValueError as exc:
        return _jsonrpc_error_result(
            body.get("id"),
            HTTPStatus.BAD_REQUEST,
            JsonRpcErrorCode.INVALID_PARAMS,
            str(exc),
        )
    except A2AError as exc:
        return _jsonrpc_error_result(
            body.get("id"),
            HTTPStatus.OK,
            _jsonrpc_code_for_a2a_error(exc),
            exc.message,
            data=exc.to_dict(),
        )


def dispatch_jsonrpc_method(
    runtime: A2ARuntime, *, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    if method == "tasks/send":
        return _send_task(runtime, params)
    if method == "tasks/get":
        return _task_payload(runtime.job_status(_task_id(params)))
    if method == "tasks/cancel":
        return _task_payload(runtime.job_cancel(_task_id(params)))
    raise ValueError(f"Unsupported A2A JSON-RPC method: {method}")


def _send_task(runtime: A2ARuntime, params: dict[str, Any]) -> dict[str, Any]:
    envelope = Envelope.new(
        from_agent=_text(params.get("fromAgent"), default="external.peer"),
        to_agent=_text(params.get("agentId"), default="openminion.local"),
        to_capability=None,
        type="job.start",
        method=_text(params.get("method"), default="tasks/send"),
        params={
            "message": params.get("message", {}),
            "metadata": params.get("metadata", {}),
        },
        timeout_ms=int(params.get("timeoutMs", 30_000) or 30_000),
        idempotency_key=_required_text(params, "idempotencyKey"),
        trace_id=_text(params.get("traceId"), default=new_uuid()),
    )
    return _task_payload(runtime.job_status(runtime.job_start(envelope)))


def _resolve_a2a_runtime(ctx: APIRouteContext) -> A2ARuntime:
    if ctx.runtime is None:
        raise A2AError("INVALID_CONFIG", "API runtime is required for external A2A")
    return resolve_external_a2a_runtime(ctx.runtime)


def _task_payload(job: JobRecord) -> dict[str, Any]:
    task = Task(
        id=job.task_id,
        state=_task_state(job.state),
        messages=_task_messages(job),
        metadata={
            "traceId": job.trace_id,
            "agentId": job.agent_id,
            "method": job.method,
            "currentStep": job.current_step,
            "progress": job.progress,
            "updatedAt": job.updated_at,
        },
    )
    return {"task": task.to_jsonable()}


def _task_messages(job: JobRecord) -> list[TaskMessage]:
    messages: list[TaskMessage] = []
    if job.result_inline is not None:
        messages.append(
            TaskMessage(
                role="agent", parts=[TaskPart(kind="data", data=job.result_inline)]
            )
        )
    if job.error is not None:
        messages.append(
            TaskMessage(
                role="agent", parts=[TaskPart(kind="data", data={"error": job.error})]
            )
        )
    return messages


def _task_state(state: str) -> TaskState:
    mapping = {
        "PENDING": TaskState.SUBMITTED,
        "RUNNING": TaskState.WORKING,
        "SUCCESS": TaskState.COMPLETED,
        "FAILED": TaskState.FAILED,
        "CANCELED": TaskState.CANCELED,
    }
    return mapping.get(str(state).upper(), TaskState.FAILED)


def _jsonrpc_error_result(
    request_id: object,
    status: HTTPStatus,
    code: int,
    message: str,
    *,
    data: object | None = None,
) -> RouteResult:
    response = JsonRpcResponse(
        id=None if request_id is None else request_id,
        error=JsonRpcError(int(code), message, data=data),
    )
    return RouteResult(status, response.to_jsonable())


def _jsonrpc_code_for_a2a_error(exc: A2AError) -> JsonRpcErrorCode:
    if exc.code == ERROR_CODE_JOB_NOT_FOUND:
        return JsonRpcErrorCode.TASK_NOT_FOUND
    return JsonRpcErrorCode.TASK_REJECTED


def _bearer_token_from_headers(headers: Mapping[str, str] | None) -> str:
    if not headers:
        return ""
    for key, value in headers.items():
        if str(key).lower() == "authorization":
            scheme, _, token = str(value).partition(" ")
            if scheme.lower() == "bearer":
                return token.strip()
    return ""


def _task_id(params: dict[str, Any]) -> str:
    return _required_text(params, "id" if "id" in params else "taskId")


def _required_text(params: dict[str, Any], name: str) -> str:
    value = str(params.get(name, "") or "").strip()
    if not value:
        raise ValueError(f"A2A parameter {name!r} is required")
    return value


def _text(value: object, *, default: str) -> str:
    text = str(value or "").strip()
    return text or default


__all__ = [
    "A2A_NETWORK_TOKEN_ENV",
    "AGENT_CARD_WELL_KNOWN_PATH",
    "EXTERNAL_A2A_RUNTIME_ATTR",
    "authorize_a2a_request",
    "build_agent_card_payload",
    "dispatch_jsonrpc_method",
    "handle_jsonrpc",
]
