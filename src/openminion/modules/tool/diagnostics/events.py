import logging
from typing import Any

from openminion.modules.telemetry.events.module import (
    emit_module_operation as _emit_module_operation_impl,
    emit_module_telemetry as _emit_module_telemetry_impl,
)

_LOGGER = logging.getLogger(__name__)
_MODULE_ID = "openminion-tool"
_ALLOWED_EXEC_OPERATIONS = frozenset(
    {
        "run",
        "stop",
        "kill",
        "poll",
        "timeout",
    }
)
_ALLOWED_INVOKE_OPERATIONS = frozenset(
    {
        "invoke",
        "validation_failed",
        "blocked_by_policy",
        "completed",
    }
)


def emit_module_telemetry(
    telemetryctl: Any,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> bool:
    return _emit_module_telemetry_impl(
        telemetryctl,
        method_name,
        *args,
        logger=_LOGGER,
        **kwargs,
    )


def emit_tool_exec_operation(
    *,
    telemetryctl: Any,
    session_id: str,
    turn_id: str,
    operation: str,
    tool_name: str,
    count: int = 1,
    status: str = "ok",
    error_code: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    normalized = str(operation or "").strip().lower()
    if normalized not in _ALLOWED_EXEC_OPERATIONS:
        return False

    payload_extra: dict[str, Any] = {}
    tool_token = str(tool_name or "").strip()
    if tool_token:
        payload_extra["tool"] = tool_token
    error_token = str(error_code or "").strip().upper()
    if error_token:
        payload_extra["error_code"] = error_token
    if extra:
        payload_extra.update(extra)

    return _emit_module_operation_impl(
        emit_module_telemetry_fn=lambda *args, **kwargs: emit_module_telemetry(
            telemetryctl,
            *args,
            **kwargs,
        ),
        session_id=session_id,
        turn_id=turn_id,
        module_id=_MODULE_ID,
        operation=normalized,
        count=count,
        status=status,
        extra=payload_extra or None,
    )


def emit_tool_invoke_operation(
    *,
    telemetryctl: Any,
    session_id: str,
    turn_id: str,
    operation: str,
    tool_name: str,
    count: int = 1,
    status: str = "ok",
    error_code: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    normalized = str(operation or "").strip().lower()
    if normalized not in _ALLOWED_INVOKE_OPERATIONS:
        return False

    payload_extra: dict[str, Any] = {}
    tool_token = str(tool_name or "").strip()
    if tool_token:
        payload_extra["tool"] = tool_token
    error_token = str(error_code or "").strip().upper()
    if error_token:
        payload_extra["error_code"] = error_token
    if extra:
        payload_extra.update(extra)

    return _emit_module_operation_impl(
        emit_module_telemetry_fn=lambda *args, **kwargs: emit_module_telemetry(
            telemetryctl,
            *args,
            **kwargs,
        ),
        session_id=session_id,
        turn_id=turn_id,
        module_id=_MODULE_ID,
        operation=normalized,
        count=count,
        status=status,
        extra=payload_extra or None,
    )


def _context_telemetry_fields(ctx: Any) -> tuple[Any, str, str]:
    extras = getattr(ctx, "extras", {}) or {}
    telemetryctl = getattr(ctx, "telemetryctl", None) or extras.get("telemetryctl")
    session_id = (
        str(getattr(ctx, "telemetry_session_id", "") or "").strip()
        or str(getattr(ctx, "session_id", "") or "").strip()
        or str(extras.get("telemetry_session_id", "") or "").strip()
        or str(extras.get("session_id", "") or "").strip()
    )
    turn_id = (
        str(getattr(ctx, "telemetry_turn_id", "") or "").strip()
        or str(getattr(ctx, "trace_id", "") or "").strip()
        or str(extras.get("telemetry_turn_id", "") or "").strip()
        or str(extras.get("turn_id", "") or "").strip()
        or str(extras.get("trace_id", "") or "").strip()
    )
    return telemetryctl, session_id, turn_id


def emit_tool_exec_operation_for_context(
    *,
    ctx: Any,
    operation: str,
    tool_name: str,
    count: int = 1,
    status: str = "ok",
    error_code: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    telemetryctl, session_id, turn_id = _context_telemetry_fields(ctx)
    if telemetryctl is None or not session_id or not turn_id:
        return False
    return emit_tool_exec_operation(
        telemetryctl=telemetryctl,
        session_id=session_id,
        turn_id=turn_id,
        operation=operation,
        tool_name=tool_name,
        count=count,
        status=status,
        error_code=error_code,
        extra=extra,
    )


def emit_tool_invoke_operation_for_context(
    *,
    ctx: Any,
    operation: str,
    tool_name: str,
    count: int = 1,
    status: str = "ok",
    error_code: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    telemetryctl, session_id, turn_id = _context_telemetry_fields(ctx)
    if telemetryctl is None or not session_id or not turn_id:
        return False
    return emit_tool_invoke_operation(
        telemetryctl=telemetryctl,
        session_id=session_id,
        turn_id=turn_id,
        operation=operation,
        tool_name=tool_name,
        count=count,
        status=status,
        error_code=error_code,
        extra=extra,
    )
