from http import HTTPStatus

from .contracts import RouteResult, exception_route_result


def task_runtime_error(exc: Exception) -> RouteResult:
    code = str(getattr(exc, "code", "") or "")
    inventory_unavailable = code == "TASK_INVENTORY_UNAVAILABLE"
    details = getattr(exc, "details", None)
    return exception_route_result(
        HTTPStatus.SERVICE_UNAVAILABLE
        if inventory_unavailable
        else HTTPStatus.INTERNAL_SERVER_ERROR,
        code=code or "task_error",
        exc=exc,
        details=dict(details) if isinstance(details, dict) else {},
        retryable=inventory_unavailable,
    )


def task_scope_denied(exc: PermissionError, *, task_id: str) -> RouteResult:
    return exception_route_result(
        HTTPStatus.FORBIDDEN,
        code="task_scope_denied",
        exc=exc,
        details={"task_id": task_id},
        retryable=False,
    )


__all__ = ["task_runtime_error", "task_scope_denied"]
