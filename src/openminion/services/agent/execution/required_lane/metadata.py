import json
from typing import TYPE_CHECKING, Any

from openminion.base.types import AgentResponse
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.registry import ToolExecutionBatch

from ..deps import ExecutorDeps
from ..state import RequiredLaneOutcome
from .state import RequiredLaneState, _PhaseResult

if TYPE_CHECKING:
    from .runner import RequiredLaneRunner


def invalid_tool_arguments_metadata(
    *,
    tool_name: str,
    missing_fields_csv: str,
) -> dict[str, str]:
    missing_fields = [
        str(field).strip()
        for field in str(missing_fields_csv or "").split(",")
        if str(field).strip()
    ]
    contract_payload = {
        "error_code": "INVALID_TOOL_ARGUMENTS",
        "reason_code": "tool_arg_validation_failed",
        "tool_name": str(tool_name or "").strip(),
        "missing_fields": missing_fields,
    }
    tool_results_payload = [
        {
            "tool_name": str(tool_name or "").strip(),
            "ok": False,
            "verified": False,
            "content": "",
            "error": "Invalid tool arguments",
            "data": {
                "error_code": "invalid_arguments",
                "reason_code": "tool_arg_validation_failed",
                "contract_error_code": "INVALID_TOOL_ARGUMENTS",
                "missing_fields": missing_fields,
            },
            "call_id": "",
            "source": "validation",
        }
    ]
    return {
        "tool_loop_termination_reason": "tool_arg_exhausted",
        "tool_arg_exhausted": str(tool_name or "").strip(),
        "tool_arg_exhausted_missing": str(missing_fields_csv or ""),
        "tool_error_code": "INVALID_TOOL_ARGUMENTS",
        "tool_error_reason_code": "tool_arg_validation_failed",
        "tool_error_payload": json.dumps(contract_payload, sort_keys=True),
        "tool_results": json.dumps(tool_results_payload, sort_keys=True),
        "tool_execution_count": "0",
    }


def build_required_outcome(
    runner: "RequiredLaneRunner",
    *,
    deps: ExecutorDeps,
    text: str,
    model: str,
    finish_reason: str,
    intent_category: str,
    termination_reason: str | None,
    tool_calls_sig: str | None,
    batch: ToolExecutionBatch | None,
    tool_calls_count: int,
    attempted_tools: list[str],
    capability_fallback_trigger_reason: str | None,
    extra_metadata: dict[str, Any] | None = None,
) -> RequiredLaneOutcome:
    metadata: dict[str, Any] = {
        "model": model,
        "finish_reason": finish_reason,
        "intent_category": intent_category or "none",
    }
    if termination_reason is not None:
        metadata["tool_loop_termination_reason"] = termination_reason
    if tool_calls_sig is not None:
        metadata["tool_calls"] = tool_calls_sig
    if batch is not None:
        metadata.update(
            deps.tool_batch_metadata(
                batch=batch,
                tool_calls_count=tool_calls_count,
            )
        )
    if extra_metadata:
        metadata.update(extra_metadata)
    metadata.update(deps.identity_metadata())
    inbound = runner.runtime.inbound
    return RequiredLaneOutcome(
        response=deps.finalize_response(
            AgentResponse(
                text=text,
                channel=inbound.channel,
                target=inbound.target,
                metadata=metadata,
            )
        ),
        attempted_tools=list(attempted_tools),
        capability_fallback_trigger_reason=capability_fallback_trigger_reason,
    )


def apply_phase_updates(target: RequiredLaneState, result: _PhaseResult) -> None:
    target.apply_updates(result.state_updates or {})


def empty_required_lane_outcome(state: RequiredLaneState) -> RequiredLaneOutcome:
    return RequiredLaneOutcome(
        response=None,
        attempted_tools=list(state.attempted_tools or []),
        capability_fallback_trigger_reason=state.capability_fallback_trigger_reason,
    )


def consume_required_phase_result(
    *,
    state: RequiredLaneState,
    result: _PhaseResult,
) -> tuple[bool, RequiredLaneOutcome | None]:
    apply_phase_updates(state, result)
    if result.action == "return":
        return True, result.outcome or empty_required_lane_outcome(state)
    if result.action == "continue":
        state.tool_to_try = result.next_tool
        return True, None
    if result.action == "break":
        state.tool_to_try = None
        return True, None
    return False, None


def first_fallback_reason(results: list[ToolExecutionResult]) -> str | None:
    for result in results:
        reason = str(getattr(result, "error", "") or "")
        if reason:
            return reason
    return None


def resolved_tool_name(default_name: str, batch: ToolExecutionBatch) -> str:
    resolved = str(default_name or "").strip()
    for result in list(batch.results or []):
        if getattr(result, "tool_name", ""):
            resolved = str(getattr(result, "tool_name", "") or "").strip() or resolved
            if getattr(result, "ok", False):
                break
    return resolved


def shared_capability_metadata(
    *,
    intent_category: str,
    capability_primary: str | None,
    tool_to_try: str,
    fallback_chain: list[str],
    attempted_tools: list[str],
    capability_fallback_trigger_reason: str | None,
    all_attempts_count: int,
) -> dict[str, Any]:
    return {
        "capability_category": intent_category or "none",
        "capability_primary": capability_primary,
        "capability_tool": tool_to_try,
        "capability_final_tool": tool_to_try,
        "capability_fallback_chain": json.dumps(fallback_chain),
        "capability_attempted_tools": json.dumps(attempted_tools),
        "capability_fallback_trigger_reason": capability_fallback_trigger_reason or "",
        "attempts_count": str(all_attempts_count),
        "fallback_used": "true" if all_attempts_count > 1 else "false",
    }


__all__ = [
    "apply_phase_updates",
    "build_required_outcome",
    "consume_required_phase_result",
    "empty_required_lane_outcome",
    "first_fallback_reason",
    "invalid_tool_arguments_metadata",
    "resolved_tool_name",
    "shared_capability_metadata",
]
