import re
import logging
from collections.abc import Mapping
from typing import Any

from openminion.modules.tool.contracts.model_ids import SECURITY_MODEL_TOOL_IDS
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
_RESULT_STATUSES = frozenset(
    {
        "blocked",
        "cancelled",
        "completed",
        "failed",
        "partial",
        "timed_out",
        "unavailable",
    }
)
_CANONICAL_ARTIFACT_REF = re.compile(r"^artifact://sha256/[0-9a-f]{64}$")
_ASSESSMENT_ID = re.compile(r"^[0-9a-f]{32}$")
_STRUCTURAL_SECURITY_TOOLS = frozenset(SECURITY_MODEL_TOOL_IDS)
_STRUCTURAL_SECURITY_AGENT_ID = "security-researcher-readonly"


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


def _operation_extra(
    tool_name: str,
    error_code: str | None,
    extra: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload: dict[str, Any] = {}
    if tool_token := tool_name.strip():
        payload["tool"] = tool_token
    if error_token := (error_code or "").strip().upper():
        payload["error_code"] = error_token
    payload.update(extra or {})
    return payload or None


def mcp_audit_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    details = data.get("details")
    sources = (data, details) if isinstance(details, Mapping) else (data,)
    fields: dict[str, Any] = {}
    for key in (
        "mcp_server",
        "mcp_remote_tool_name",
        "runtime_tool_name",
        "approval_mode",
        "approval_required",
        "reason_code",
    ):
        for source in sources:
            if key in source and source[key] not in (None, ""):
                fields[key] = source[key]
                break
    if fields.get("mcp_server") and fields.get("mcp_remote_tool_name"):
        fields["mcp_primitive"] = "tools"
    return fields


def structural_result_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    result_status = data.get("execution_status") or data.get("status")
    if isinstance(result_status, str):
        if result_status in _RESULT_STATUSES:
            fields["result_status"] = result_status
    assessment_id = data.get("assessment_id")
    if isinstance(assessment_id, str) and _ASSESSMENT_ID.fullmatch(assessment_id):
        fields["assessment_id"] = assessment_id
    for key in (
        "total_findings",
        "returned_findings",
        "check_count",
        "finding_count",
        "candidate_count",
        "rejected_count",
        "redaction_count",
        "duration_ms",
    ):
        value = data.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            fields[key] = value
    refs = data.get("artifact_refs")
    if isinstance(refs, list):
        artifact_refs = [
            item
            for item in refs
            if isinstance(item, str) and _CANONICAL_ARTIFACT_REF.fullmatch(item)
        ]
        if artifact_refs:
            fields["artifact_refs"] = artifact_refs
            fields["artifact_count"] = len(artifact_refs)
    return fields


def is_structural_security_tool(tool_name: str) -> bool:
    return tool_name in _STRUCTURAL_SECURITY_TOOLS


def is_structural_security_agent(agent_id: str) -> bool:
    return str(agent_id or "").strip() == _STRUCTURAL_SECURITY_AGENT_ID


def structural_security_tool_result(
    item: Mapping[str, Any],
) -> dict[str, Any] | None:
    tool_name = str(item.get("tool_name") or "")
    if not is_structural_security_tool(tool_name):
        return None
    return structural_tool_result(item)


def structural_tool_result(item: Mapping[str, Any]) -> dict[str, Any]:
    tool_name = str(item.get("tool_name") or "")
    data = item.get("data")
    return {
        "structural_only": True,
        "tool_name": tool_name,
        "ok": bool(item.get("ok")),
        "verified": bool(item.get("verified")),
        "data": structural_result_fields(data if isinstance(data, Mapping) else {}),
        "error_code": str(item.get("error_code") or ""),
        "call_id": str(item.get("call_id") or ""),
        "source": str(item.get("source") or ""),
    }


def structural_security_tool_results(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [structural_security_tool_result(item) or item for item in items]


def structural_tool_results(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [structural_tool_result(item) for item in items]


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
    normalized = operation.strip().lower()
    if normalized not in _ALLOWED_EXEC_OPERATIONS:
        return False

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
        extra=_operation_extra(tool_name, error_code, extra),
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
    normalized = operation.strip().lower()
    if normalized not in _ALLOWED_INVOKE_OPERATIONS:
        return False

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
        extra=_operation_extra(tool_name, error_code, extra),
    )


def _context_telemetry_fields(ctx: Any) -> tuple[Any, str, str]:
    extras = getattr(ctx, "extras", {}) or {}
    metadata = getattr(ctx, "metadata", {}) or {}
    telemetryctl = getattr(ctx, "telemetryctl", None) or extras.get("telemetryctl")
    session_id = (
        str(getattr(ctx, "telemetry_session_id", "") or "").strip()
        or str(getattr(ctx, "session_id", "") or "").strip()
        or str(extras.get("telemetry_session_id", "") or "").strip()
        or str(extras.get("session_id", "") or "").strip()
        or str(metadata.get("session_id", "") or "").strip()
    )
    turn_id = (
        str(getattr(ctx, "telemetry_turn_id", "") or "").strip()
        or str(getattr(ctx, "trace_id", "") or "").strip()
        or str(extras.get("telemetry_turn_id", "") or "").strip()
        or str(extras.get("turn_id", "") or "").strip()
        or str(extras.get("trace_id", "") or "").strip()
        or str(metadata.get("turn_id", "") or "").strip()
        or str(metadata.get("trace_id", "") or "").strip()
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


def emit_tool_execution_event(
    *,
    ctx: Any,
    event_type: str,
    payload: Mapping[str, Any],
    status: str,
    error: Mapping[str, Any] | None = None,
) -> bool:
    telemetryctl, session_id, turn_id = _context_telemetry_fields(ctx)
    if telemetryctl is None or not session_id or not turn_id:
        return False
    metadata = getattr(ctx, "metadata", {}) or {}
    event_payload = dict(payload)
    for key in ("invocation_id", "execution_id", "agent_id"):
        value = metadata.get(key)
        if value:
            event_payload[key] = str(value)
    return emit_module_telemetry(
        telemetryctl,
        "emit_canonical_event",
        session_id,
        turn_id,
        event_type,
        event_payload,
        trace_id=str(metadata.get("trace_id") or ""),
        status=status,
        error=dict(error) if error else None,
    )
