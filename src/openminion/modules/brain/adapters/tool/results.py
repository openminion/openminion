import json
import time
from collections.abc import Mapping
from typing import Any

from openminion.base.logging import get_logger
from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_JOB_STATUS_RUNNING,
    BRAIN_STATE_ERROR,
)
from openminion.modules.tool import RuntimeContext, ToolSpec, preferred_artifact_ref
from openminion.modules.tool.diagnostics.events import emit_tool_execution_event
from openminion.modules.tool.errors import ToolRuntimeError

_log = get_logger("brain.adapters.tool.runtime")


def _normalized_summary_token(value: Any, *, limit: int = 600) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    if len(token) <= limit:
        return token
    return token[:limit].rstrip() + "..."


def _derive_toolspec_summary(
    payload: Any, *, status: str, tool_name: str = "unknown"
) -> str:
    success = status == BRAIN_ACTION_STATUS_SUCCESS
    if not isinstance(payload, Mapping):
        return "Tool executed successfully" if success else "Tool execution failed"
    data_field = payload.get("data")
    outputs_field = payload.get("outputs")
    mappings: list[Mapping[str, Any]] = [payload]
    if isinstance(data_field, Mapping):
        mappings.append(data_field)
    if isinstance(outputs_field, Mapping):
        mappings.append(outputs_field)
    if success:
        for key in ("summary", "content", "message", "answer"):
            for mapping in mappings:
                token = _normalized_summary_token(mapping.get(key))
                if token:
                    return token
        synth_source = (
            data_field if isinstance(data_field, Mapping) and data_field else payload
        )
        if synth_source:
            try:
                synthesized = _normalized_summary_token(
                    json.dumps(synth_source, sort_keys=True, default=str)
                )
            except (TypeError, ValueError):
                synthesized = _normalized_summary_token(synth_source)
            if synthesized and synthesized not in {"{}", "[]"}:
                return synthesized
        _log.warning("tool.summary.generic_fallback tool=%s", tool_name)
        return "Tool executed successfully"
    for key in ("summary", "content", "message"):
        for mapping in mappings:
            token = _normalized_summary_token(mapping.get(key))
            if token:
                return token
    raw_error = payload.get("error")
    if isinstance(raw_error, Mapping):
        error_message = _normalized_summary_token(
            raw_error.get("message") or raw_error.get("code")
        )
        if error_message:
            return error_message
    elif raw_error:
        error_message = _normalized_summary_token(raw_error)
        if error_message:
            return error_message
    return "Tool execution failed"


def _normalized_artifact_refs(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        ref = preferred_artifact_ref(item)
        if not ref or ref in seen:
            continue
        seen.add(ref)
        refs.append({"ref": ref, "role": "output"})
    return refs


def _error_envelope(
    *,
    status: str,
    summary: str,
    code: str,
    message: str,
    latency_ms: int = 0,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {
        "status": status,
        "summary": summary,
        "outputs": {},
        "artifact_refs": [],
        "memory_refs": [],
        "metrics": {
            "latency_ms": latency_ms,
            "tokens_used": 0,
            "cost_estimate": 0.0,
        },
        "error": error,
    }


def _tool_allowlist_error(tool_name: str) -> dict[str, Any]:
    return _error_envelope(
        status=BRAIN_STATE_ERROR,
        summary="Tool is not allowed for this turn",
        code="POLICY_DENIED",
        message=f"Tool '{tool_name}' is outside the turn tool allowlist.",
        details={"tool_name": tool_name},
    )


def run_tool_spec(
    *,
    spec: ToolSpec,
    validated_args: dict[str, Any],
    context: RuntimeContext,
    start_time: float,
    background_write_authorized: bool,
    tool_name: str,
) -> dict[str, Any]:
    event_payload = {"tool_call_id": context.tool_call_id, "tool_name": tool_name}
    emit_tool_execution_event(
        ctx=context,
        event_type="tool.execution.started",
        status="running",
        payload=event_payload,
    )
    try:
        data = spec.handler(validated_args, context)
    except ToolRuntimeError:
        emit_tool_execution_event(
            ctx=context,
            event_type="tool.execution.failed",
            status="failed",
            payload=event_payload,
        )
        raise
    except Exception as exc:
        emit_tool_execution_event(
            ctx=context,
            event_type="tool.execution.failed",
            status="failed",
            payload=event_payload,
        )
        return _error_envelope(
            status=BRAIN_STATE_ERROR,
            summary="Tool execution failed",
            code="EXEC_ERROR",
            message="Tool execution failed",
            latency_ms=int((time.monotonic() - start_time) * 1000),
            details={"error_type": type(exc).__name__},
        )
    if isinstance(data, Mapping) and isinstance(data.get("ok"), bool):
        inner_status = "ok" if data["ok"] else BRAIN_STATE_ERROR
    elif isinstance(data, Mapping) and "status" in data:
        inner_status = str(data.get("status", BRAIN_STATE_ERROR))
    else:
        inner_status = "ok"
    status = (
        BRAIN_ACTION_STATUS_SUCCESS
        if inner_status in ("ok", BRAIN_ACTION_STATUS_SUCCESS, BRAIN_JOB_STATUS_RUNNING)
        else BRAIN_STATE_ERROR
    )
    summary = _derive_toolspec_summary(data, status=status, tool_name=spec.name)
    result = {
        "status": status,
        "summary": summary,
        "outputs": data,
        "artifact_refs": _normalized_artifact_refs(context.artifacts),
        "memory_refs": [],
        "metrics": {
            "latency_ms": int((time.monotonic() - start_time) * 1000),
            "tokens_used": 0,
            "cost_estimate": 0.0,
        },
    }
    if background_write_authorized:
        result["outputs"] = dict(result["outputs"])
        result["outputs"].update(
            background_watch_write_authorized=True,
            background_watch_write_tool=tool_name,
        )
    if status != BRAIN_ACTION_STATUS_SUCCESS:
        raw_error = data.get("error") if isinstance(data, Mapping) else None
        if isinstance(raw_error, Mapping):
            error: dict[str, Any] = {
                "code": str(raw_error.get("code", "") or "EXEC_ERROR"),
                "message": str(raw_error.get("message", "") or summary).strip(),
            }
            if isinstance(raw_error.get("details"), Mapping):
                error["details"] = dict(raw_error["details"])
        elif raw_error:
            error = {"code": "EXEC_ERROR", "message": str(raw_error).strip()}
        else:
            error = {"code": "EXEC_ERROR", "message": summary}
        result["error"] = error
    succeeded = status == BRAIN_ACTION_STATUS_SUCCESS
    emit_tool_execution_event(
        ctx=context,
        event_type=(
            "tool.execution.completed" if succeeded else "tool.execution.failed"
        ),
        status="succeeded" if succeeded else "failed",
        payload=event_payload,
    )
    return result


__all__ = [
    "_derive_toolspec_summary",
    "_error_envelope",
    "_normalized_artifact_refs",
    "_tool_allowlist_error",
    "run_tool_spec",
]
