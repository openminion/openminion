from __future__ import annotations

import json
from typing import Any

from openminion.base.config.core import resolve_default_agent_id
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.llm.providers.envelope_v2 import CONTRACT_VERSION_V2
from openminion.services.brain.post_execution.usage import (
    collect_llm_usage_totals_from_events,
)

_STRUCTURED_ACTION_OUTPUT_METADATA_KEYS: tuple[str, ...] = (
    "adaptive.finalization_status",
    "pending_turn_context",
    "meta_rule_preference",
    "session_work_summary",
    "goal_declaration",
    "goal_revision",
    "task_plan",
    "task_plan.step_completed",
    "task_plan.step_blocked",
    "task_plan.revision",
    "task_plan.abandoned",
    "task_plan.completed",
)



def _build_turn_response_metadata(
    self,
    *,
    runner: BrainRunner,
    step_out: Any,
    session_id: str,
    request_id: str | None,
    elapsed_ms: float,
    llm_steps: int,
    termination_reason: str,
) -> dict[str, str]:
    llm_call_counts = self._collect_llm_call_counts_by_purpose(
        runner=runner,
        session_id=session_id,
        trace_id=request_id,
        fallback_total_llm_calls=max(0, int(llm_steps)),
    )
    action_outputs = getattr(getattr(step_out, "action_result", None), "outputs", None)
    if not isinstance(action_outputs, dict):
        action_outputs = {}
    total_input_tokens_used = int(action_outputs.get("total_input_tokens_used", 0) or 0)
    total_output_tokens_used = int(
        action_outputs.get("total_output_tokens_used", 0) or 0
    )
    total_tokens_used = int(action_outputs.get("total_tokens_used", 0) or 0)
    if total_tokens_used <= 0:
        (
            event_input_tokens,
            event_output_tokens,
            event_total_tokens,
        ) = collect_llm_usage_totals_from_events(
            runner=runner,
            session_id=session_id,
            trace_id=request_id,
        )
        if event_total_tokens > 0:
            total_input_tokens_used = event_input_tokens
            total_output_tokens_used = event_output_tokens
            total_tokens_used = event_total_tokens
    tool_calls_count = int(action_outputs.get("tool_calls_count", 0) or 0)
    _default_agent_id = resolve_default_agent_id(self._config)
    return {
        "agent": self._config.agents[_default_agent_id].name or _default_agent_id,
        "provider": str(getattr(self._provider, "name", "brain-orchestrator")),
        "model": "brain-orchestrator",
        "inference_steps": str(max(llm_steps, 1)),
        "tool_loop_max_steps": "16",
        "tool_loop_termination_reason": termination_reason,
        "elapsed_ms": str(int(elapsed_ms)),
        "turn_duration_ms": str(int(elapsed_ms)),
        "brain_status": str(getattr(step_out, "status", "completed")),
        "llm_call_counts_by_purpose": json.dumps(llm_call_counts, sort_keys=True),
        "llm_calls_count": str(sum(int(v) for v in llm_call_counts.values())),
        "tool_calls_count": str(tool_calls_count),
        "total_input_tokens_used": str(total_input_tokens_used),
        "total_output_tokens_used": str(total_output_tokens_used),
        "total_tokens_used": str(total_tokens_used),
    }


def _security_events_from_tool_results(
    *,
    tool_results_payload: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], dict[str, str]]:
    security_events: list[dict[str, str]] = []
    metadata_updates: dict[str, str] = {}
    for item in tool_results_payload:
        data = item.get("data", {})
        if not isinstance(data, dict):
            data = {}
        error_code = str(
            item.get("error_code", "") or data.get("error_code", "")
        ).strip()
        if not error_code:
            continue
        blocked_kind = str(
            item.get("blocked_kind", "") or data.get("blocked_kind", "")
        ).strip()
        event_kind = blocked_kind or (
            "approval_required" if error_code == "require_approval" else "policy_denied"
        )
        reason_code = (
            str(item.get("reason_code", "") or data.get("reason_code", "")).strip()
            or error_code
        )
        details = {}
        raw_details = data.get("error_details")
        if isinstance(raw_details, dict):
            details = raw_details
        security_events.append(
            {
                "event_kind": event_kind,
                "reason_code": reason_code,
                "policy_version": str(details.get("policy_version", "") or "v1"),
                "decision": str(details.get("decision", "") or error_code),
                "tool_name": str(
                    item.get("tool_name", "") or item.get("name", "") or ""
                ),
                "call_id": str(item.get("call_id", "") or item.get("id", "") or ""),
                "source": "policy",
            }
        )
        if error_code.startswith("tool_budget") and details:
            metadata_updates["tool_budget"] = json.dumps(details, sort_keys=True)
    return security_events, metadata_updates


def _attach_tool_result_metadata(
    self,
    *,
    metadata: dict[str, str],
    tool_results_payload: list[dict[str, Any]],
    termination_reason: str,
) -> None:
    if not tool_results_payload:
        return
    metadata["tool_contract_version"] = CONTRACT_VERSION_V2
    ok_all = all(bool(item.get("ok")) for item in tool_results_payload)
    metadata["tool_calls_count"] = str(len(tool_results_payload))
    metadata["tool_execution_count"] = str(len(tool_results_payload))
    metadata["tool_verified"] = str(
        all(bool(item.get("verified")) for item in tool_results_payload)
    ).lower()
    metadata["tool_results"] = json.dumps(
        tool_results_payload,
        sort_keys=True,
        default=str,
    )
    if not ok_all:
        metadata["tool_loop_termination_reason"] = (
            termination_reason
            if termination_reason
            and termination_reason not in {"tool_final", "model_final"}
            else "tool_no_success"
        )
    else:
        metadata["tool_loop_termination_reason"] = (
            "tool_final" if termination_reason == "tool_final" else "model_final"
        )
    security_events, metadata_updates = _security_events_from_tool_results(
        tool_results_payload=tool_results_payload
    )
    metadata.update(metadata_updates)
    if security_events:
        metadata["security_events"] = json.dumps(security_events, sort_keys=True)


def _attach_cumulative_tool_result_metadata(
    *,
    metadata: dict[str, str],
    tool_results_payload: list[dict[str, Any]],
) -> None:
    if not tool_results_payload:
        return
    metadata["tool_calls_count_cumulative"] = str(len(tool_results_payload))
    metadata["tool_execution_count_cumulative"] = str(len(tool_results_payload))
    metadata["tool_calls_cumulative"] = json.dumps(
        tool_results_payload,
        sort_keys=True,
        default=str,
    )


def _attach_watch_outcome_metadata(
    *,
    metadata: dict[str, str],
    action_result: Any | None,
) -> None:
    if action_result is None:
        return
    outputs = getattr(action_result, "outputs", None)
    if not isinstance(outputs, dict):
        return
    if "watch.condition_met" not in outputs and "watch.summary" not in outputs:
        return
    condition_met = bool(outputs.get("watch.condition_met", False))
    summary = str(outputs.get("watch.summary", "") or "").strip()
    metadata["watch_condition_met"] = str(condition_met).lower()
    metadata["watch_outcome"] = json.dumps(
        {"condition_met": condition_met, "summary": summary},
        sort_keys=True,
    )
    if summary:
        metadata["watch_summary"] = summary


def _attach_delegation_result_metadata(
    *,
    metadata: dict[str, str],
    action_result: Any | None,
) -> None:
    if action_result is None:
        return
    outputs = getattr(action_result, "outputs", None)
    if not isinstance(outputs, dict):
        return
    result_summary = outputs.get("delegation_result_summary")
    if not isinstance(result_summary, dict):
        return
    metadata["delegation_result_summary"] = json.dumps(
        result_summary,
        sort_keys=True,
        default=str,
    )


def _attach_structured_action_output_metadata(
    *,
    metadata: dict[str, str],
    action_result: Any | None,
) -> None:
    if action_result is None:
        return
    outputs = getattr(action_result, "outputs", None)
    if not isinstance(outputs, dict):
        return
    for key in _STRUCTURED_ACTION_OUTPUT_METADATA_KEYS:
        if key not in outputs:
            continue
        value = outputs.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            metadata[key] = str(value).lower()
            continue
        if isinstance(value, (dict, list)):
            metadata[key] = json.dumps(
                value,
                sort_keys=True,
                default=str,
            )
            continue
        token = str(value).strip()
        if token:
            metadata[key] = token
