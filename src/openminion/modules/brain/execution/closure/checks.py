from typing import Any

from ...schemas.decisions import FinalizationStatus
from ...schemas.state import ActionResult, WorkingState

_CLOSURE_TOOL_RESULT_DATA_KEYS = frozenset(
    {
        "path",
        "mode",
        "bytes_written",
        "source",
        "argv",
        "exit_code",
        "status",
        "missing_fields",
        "reason_code",
        "query_time",
        "url",
    }
)


def _closure_tool_result_snapshot(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    data = item.get("data")
    normalized: dict[str, Any] = {}
    tool_name = str(
        item.get("tool_name") or item.get("name") or item.get("tool") or ""
    ).strip()
    if tool_name:
        normalized["tool_name"] = tool_name
    if "ok" in item:
        normalized["ok"] = bool(item.get("ok"))
    if "verified" in item:
        normalized["verified"] = bool(item.get("verified"))
    error_code = str(item.get("error_code", "") or "").strip()
    if error_code:
        normalized["error_code"] = error_code
    reason_code = str(item.get("reason_code", "") or "").strip()
    if reason_code:
        normalized["reason_code"] = reason_code
    if isinstance(data, dict):
        filtered_data = {
            key: value
            for key, value in data.items()
            if key in _CLOSURE_TOOL_RESULT_DATA_KEYS
        }
        if filtered_data:
            normalized["data"] = filtered_data
    return normalized or None


def _closure_action_outputs(action_result: ActionResult | None) -> dict[str, Any]:
    outputs = getattr(action_result, "outputs", None)
    if not isinstance(outputs, dict):
        return {}
    payload: dict[str, Any] = {}
    tool_results = [
        snapshot
        for snapshot in (
            _closure_tool_result_snapshot(item)
            for item in list(outputs.get("tool_results", []) or [])
        )
        if snapshot is not None
    ]
    if tool_results:
        payload["tool_results"] = tool_results[-12:]
        payload["tool_execution_count"] = len(tool_results)
        payload["tool_name_sequence"] = [
            str(item.get("tool_name", "") or "").strip()
            for item in tool_results[-12:]
            if str(item.get("tool_name", "") or "").strip()
        ]
    for key in (
        "adaptive.termination_reason",
        "adaptive.tool_calls",
        "adaptive.tool_calls_total",
        "coding.current_phase",
        "coding.plan_phases_executed",
        "coding.open_issues_count",
        "coding.verify_gate_reason",
        "coding.verifier_verdict",
        "pending_turn_context",
        "adaptive.finalization_status",
        "task_plan",
        "task_plan.step_completed",
        "task_plan.step_blocked",
    ):
        value = outputs.get(key)
        if value not in (None, "", [], {}):
            payload[key] = value
    return payload


def _has_successful_mutation_tool_evidence(
    action_result: ActionResult | None,
) -> bool:
    outputs = dict(getattr(action_result, "outputs", {}) or {}) if action_result else {}
    tool_results = [
        item
        for item in list(outputs.get("tool_results", []) or [])
        if isinstance(item, dict)
    ]
    mutation_tools = {"file.write", "file.edit", "model.file_write", "model.file_edit"}
    return any(
        bool(item.get("ok"))
        and str(item.get("tool_name", "") or "").strip() in mutation_tools
        for item in tool_results
    )


def _closure_finalization_status(
    action_result: ActionResult | None,
) -> FinalizationStatus | None:
    outputs = getattr(action_result, "outputs", None)
    if not isinstance(outputs, dict):
        return None
    raw_status = outputs.get("adaptive.finalization_status")
    if isinstance(raw_status, FinalizationStatus):
        return raw_status
    if not isinstance(raw_status, dict):
        return None
    try:
        return FinalizationStatus.model_validate(raw_status)
    except ValueError:
        return None


def _can_continue_for_freshness(state: WorkingState) -> bool:
    budgets = getattr(state, "budgets_remaining", None)
    return (
        int(getattr(budgets, "tool_calls", 0) or 0) > 0
        and int(getattr(budgets, "tokens", 0) or 0) > 0
        and int(getattr(budgets, "time_ms", 0) or 0) > 0
    )
