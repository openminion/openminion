"""Tool-loop observation and progress emission."""

import time
from typing import Any

from openminion.modules.tool.base import ToolExecutionContext, ToolExecutionResult

from .loop_quality import observe_tool_calls
from .ports import ProviderToolCall, TurnFlowServicePort


def _emit_started(callback: Any, calls: list[ProviderToolCall]) -> None:
    for call in calls:
        try:
            callback(
                {
                    "kind": "tool_started",
                    "tool_name": str(getattr(call, "name", "") or ""),
                    "args": dict(getattr(call, "arguments", {}) or {}),
                    "call_id": str(getattr(call, "id", "") or ""),
                    "model_tool_name": "",
                    "runtime_tool_name": "",
                    "runtime_binding_id": "",
                    "runtime_fallback_used": False,
                    "runtime_fallback_chain": [],
                    "runtime_resolution_source": "",
                    "fallback_index": 0,
                    "state": "running",
                }
            )
        except Exception:
            pass


def _completed_event(
    result: ToolExecutionResult,
    call: ProviderToolCall | None,
    batch_duration_ms: int,
) -> dict[str, Any]:
    result_data = getattr(result, "data", {}) or {}
    duration_ms = getattr(result, "duration_ms", None)
    return {
        "kind": "tool_completed",
        "tool_name": str(
            getattr(result, "tool_name", "") or getattr(call, "name", "") or ""
        ),
        "args": dict(getattr(call, "arguments", {}) or {}),
        "call_id": str(getattr(result, "call_id", "") or getattr(call, "id", "") or ""),
        "content": str(
            getattr(result, "content", "")
            or getattr(result, "data", "")
            or getattr(result, "error", "")
            or ""
        ),
        "ok": bool(getattr(result, "ok", False)),
        "duration_ms": int(batch_duration_ms if duration_ms is None else duration_ms),
        "batch_duration_ms": batch_duration_ms,
        "exit_code": 0 if bool(getattr(result, "ok", False)) else 1,
        "truncated": False,
        "model_tool_name": str(result_data.get("model_tool_name", "") or ""),
        "runtime_tool_name": str(result_data.get("runtime_tool_name", "") or ""),
        "runtime_binding_id": str(result_data.get("runtime_binding_id", "") or ""),
        "runtime_fallback_used": bool(result_data.get("runtime_fallback_used", False)),
        "runtime_fallback_chain": list(
            result_data.get("runtime_fallback_chain", []) or []
        ),
        "runtime_resolution_source": str(
            result_data.get("runtime_resolution_source", "") or ""
        ),
        "fallback_index": int(getattr(result, "fallback_index", 0) or 0),
        "state": str(getattr(result, "state", "ok") or "ok"),
    }


def _emit_completed(
    callback: Any,
    results: list[ToolExecutionResult],
    calls: list[ProviderToolCall],
    batch_duration_ms: int,
) -> None:
    for index, result in enumerate(results):
        call = calls[index] if index < len(calls) else None
        try:
            callback(_completed_event(result, call, batch_duration_ms))
        except Exception:
            pass


def execute_allowed_tool_calls(
    service_port: TurnFlowServicePort,
    runtime: Any,
    *,
    allowed_calls: list[ProviderToolCall],
    context: ToolExecutionContext,
) -> list[ToolExecutionResult]:
    if not allowed_calls:
        return []
    callback = getattr(runtime, "progress_callback", None)
    started_at = time.perf_counter()
    if callable(callback):
        _emit_started(callback, allowed_calls)
    batch = service_port.tools.execute_calls(allowed_calls, context=context)
    if callable(callback):
        _emit_completed(
            callback,
            list(batch.results or []),
            allowed_calls,
            int((time.perf_counter() - started_at) * 1000),
        )
    return list(batch.results)


def observe_tool_loop(runtime: Any, tool_calls: list[ProviderToolCall]) -> None:
    observations = observe_tool_calls(
        tool_calls,
        seen_signatures=getattr(runtime, "tool_call_signature_counts", None),
    )
    if not observations:
        return
    runtime_observations = getattr(runtime, "tool_loop_observations", None)
    if isinstance(runtime_observations, list):
        runtime_observations.extend(observations)
    callback = getattr(runtime, "progress_callback", None)
    if not callable(callback):
        return
    for observation in observations:
        try:
            callback(dict(observation))
        except Exception:
            pass


__all__ = ["execute_allowed_tool_calls", "observe_tool_loop"]
