from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_BLOCKED,
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_ACTION_STATUS_TIMEOUT,
)
from openminion.modules.brain.execution.intent_state import (
    build_raw_intent_execution_state_block,
)
from openminion.modules.brain.schemas import ActionResult
from openminion.modules.llm.schemas import Message

from ..contracts import (
    AdaptiveToolLoopContext,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    RawToolResult,
)
from ..budget_control import _general_profile_name
from ..direct_tool import _direct_tool_turn_active
from ..evidence import (
    _count_substantive_non_control_tool_results,
    _loop_tool_result_payloads,
)

if TYPE_CHECKING:
    from typing import Callable


_ANSWER_ONLY_FINALIZATION_KEYS = frozenset(
    {
        "budget_answer_only_finalization_forced",
        "circular_pattern_answer_only_finalization_forced",
        "duplicate_batch_answer_only_closure_forced",
        "duplicate_batch_answer_only_closure_pending",
        "iteration_cap_answer_only_finalization_forced",
        "mutating_file_answer_only_closure_pending",
    }
)
_MUTATING_FILE_TOOLS = frozenset(
    {
        "file.write",
        "file.edit",
        "code.patch",
        "model.file_write",
        "model.file_edit",
    }
)
_SYNTHESIS_TOOL_PREFIXES = ("web.", "browser.", "research.")


def _requires_typed_finalization_contract(
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
) -> bool:
    coding_profile = str(getattr(profile, "profile_name", "") or "").strip() == (
        "coding_v1"
    )
    scratchpad = getattr(loop_state, "scratchpad", {}) or {}
    if any(bool(scratchpad.get(key, False)) for key in _ANSWER_ONLY_FINALIZATION_KEYS):
        return False
    direct_tool_turn_active = _direct_tool_turn_active(loop_state)
    substantive_count = _count_substantive_non_control_tool_results(loop_state)
    if substantive_count <= 0:
        return (
            _general_profile_name(profile)
            and not direct_tool_turn_active
            and not list(getattr(loop_state, "messages", []) or [])
        )
    tool_names = {
        str(item.get("tool_name", "") or "").strip()
        for item in _loop_tool_result_payloads(loop_state)
        if str(item.get("tool_name", "") or "").strip()
    }
    if _general_profile_name(profile) and not direct_tool_turn_active:
        return True
    if (
        not coding_profile
        and not direct_tool_turn_active
        and any(name in _MUTATING_FILE_TOOLS for name in tool_names)
    ):
        return True
    if substantive_count >= 3 and any(
        name.startswith(_SYNTHESIS_TOOL_PREFIXES) for name in tool_names
    ):
        return True
    return False


def _tool_result_payload_from_action(
    *,
    call_id: str,
    tool_name: str,
    action_result: ActionResult,
) -> dict[str, Any]:
    status = str(getattr(action_result, "status", "") or "").strip().lower()
    ok = status == BRAIN_ACTION_STATUS_SUCCESS
    data = dict(getattr(action_result, "outputs", {}) or {})
    error_message = ""
    error_code = ""
    error_details: dict[str, Any] = {}
    error_obj = getattr(action_result, "error", None)
    if error_obj is not None:
        if isinstance(error_obj, dict):
            error_message = str(error_obj.get("message", "") or "")
            error_code = str(error_obj.get("code", "") or "")
            raw_details = error_obj.get("details")
            if isinstance(raw_details, dict):
                error_details = dict(raw_details)
        else:
            error_message = str(getattr(error_obj, "message", "") or "")
            error_code = str(getattr(error_obj, "code", "") or "")
            raw_details = getattr(error_obj, "details", None)
            if isinstance(raw_details, dict):
                error_details = dict(raw_details)
    if not error_message and not ok:
        error_message = str(data.get("error", "") or "")
    if not error_message and not ok:
        error_message = str(getattr(action_result, "summary", "") or status)
    if error_code:
        data["error_code"] = error_code
    if error_details:
        data["error_details"] = error_details
    return {
        "tool_name": str(tool_name or "").strip() or "unknown",
        "ok": ok,
        "verified": ok,
        "content": str(getattr(action_result, "summary", "") or ""),
        "error": error_message,
        "data": data,
        "error_code": error_code,
        "call_id": call_id,
        "source": "native",
    }


def _execute_prepared_tool_dispatch_from_context(
    ctx: Any,
    *,
    prepared_dispatch: Any,
) -> RawToolResult:
    execute_fn = getattr(ctx.command_executor, "execute_prepared_tool_dispatch", None)
    if callable(execute_fn):
        return execute_fn(prepared_dispatch=prepared_dispatch)
    outcome = ctx.command_executor.execute_command(
        state=ctx.state,
        command=prepared_dispatch.approved_command,
        logger=ctx.logger,
        include_reflect=False,
    )
    return RawToolResult(
        command_id=prepared_dispatch.command_id,
        tool_name=prepared_dispatch.tool_name,
        raw_output=outcome,
    )


def _finalize_tool_result_from_context(
    ctx: Any,
    *,
    prepared_dispatch: Any,
    raw_result: Any,
    postprocess_outcome: Callable[..., Any],
) -> Any:
    finalize_fn = getattr(ctx.command_executor, "finalize_tool_result", None)
    if callable(finalize_fn):
        outcome = finalize_fn(
            state=ctx.state,
            prepared_dispatch=prepared_dispatch,
            raw_result=raw_result,
            logger=ctx.logger,
        )
    else:
        outcome = raw_result.raw_output
    return postprocess_outcome(
        outcome,
        original_command=getattr(prepared_dispatch, "original_command", None),
    )


def _append_tool_result_payload(
    loop_state: AdaptiveToolLoopState,
    *,
    call_id: str,
    tool_name: str,
    action_result: ActionResult,
) -> None:
    scratchpad = dict(loop_state.scratchpad or {})
    results = [
        item
        for item in list(scratchpad.get("adaptive.tool_results", []) or [])
        if isinstance(item, dict)
    ]
    results.append(
        _tool_result_payload_from_action(
            call_id=call_id,
            tool_name=tool_name,
            action_result=action_result,
        )
    )
    scratchpad["adaptive.tool_results"] = results
    loop_state.scratchpad = scratchpad


def _set_turn_progress(
    loop_state: AdaptiveToolLoopState,
    *,
    llm_call_count: int | None = None,
    llm_call_limit: int | None = None,
    input_tokens_delta: int = 0,
    output_tokens_delta: int = 0,
    progress_phase: str | None = None,
    tool_name: str | None = None,
    detail_code: str | None = None,
) -> None:
    scratchpad = dict(loop_state.scratchpad or {})
    input_total = int(scratchpad.get("turn_progress_input_tokens_total", 0) or 0)
    output_total = int(scratchpad.get("turn_progress_output_tokens_total", 0) or 0)
    if input_tokens_delta:
        input_total += int(input_tokens_delta)
    if output_tokens_delta:
        output_total += int(output_tokens_delta)
    scratchpad["turn_progress_input_tokens_total"] = input_total
    scratchpad["turn_progress_output_tokens_total"] = output_total
    scratchpad["turn_progress_total_tokens_used"] = input_total + output_total
    if llm_call_count is not None:
        scratchpad["turn_progress_llm_call_count"] = max(0, int(llm_call_count))
    if llm_call_limit is not None:
        scratchpad["turn_progress_llm_call_limit"] = max(0, int(llm_call_limit))
    if progress_phase is not None:
        scratchpad["turn_progress_phase"] = str(progress_phase or "").strip()
    if tool_name is not None:
        scratchpad["turn_progress_tool_name"] = str(tool_name or "").strip()
    if detail_code is not None:
        scratchpad["turn_progress_detail_code"] = str(detail_code or "").strip()
    loop_state.scratchpad = scratchpad


def _build_enrichment_message(
    tool_name: str, score: float, result_summary: str
) -> Message:
    truncated = result_summary[:200] + ("..." if len(result_summary) > 200 else "")
    return Message(
        role="system",
        content=(
            f"[system] Tool {tool_name} returned an anomalous result"
            f" (score: {score:.2f}): {truncated}. Review before proceeding."
        ),
    )


def _build_tool_failure_recovery_message(
    *,
    tool_name: str,
    action_result: ActionResult,
) -> Message | None:
    status = str(getattr(action_result, "status", "") or "").strip().lower()
    error_obj = getattr(action_result, "error", None)
    error_message = str(getattr(error_obj, "message", "") or "").strip()
    error_code = str(getattr(error_obj, "code", "") or "").strip()
    details_payload = getattr(error_obj, "details", None)
    outputs = getattr(action_result, "outputs", None)
    nested_error = outputs.get("error") if isinstance(outputs, dict) else None
    if not error_code and isinstance(nested_error, dict):
        error_code = str(nested_error.get("code", "") or "").strip()
    if not error_message and isinstance(nested_error, dict):
        error_message = str(nested_error.get("message", "") or "").strip()
    details_dict = details_payload if isinstance(details_payload, dict) else {}
    if not details_dict and isinstance(nested_error, dict):
        nested_details = nested_error.get("details")
        if isinstance(nested_details, dict):
            details_dict = nested_details
    summary = str(getattr(action_result, "summary", "") or "").strip()
    if status not in {
        BRAIN_ACTION_STATUS_BLOCKED,
        BRAIN_ACTION_STATUS_FAILED,
        BRAIN_ACTION_STATUS_TIMEOUT,
    }:
        return None
    details = error_message or summary or "The tool call failed."
    code_suffix = f" (code={error_code})" if error_code else ""
    details_suffix = (
        f" details={json.dumps(details_dict, ensure_ascii=False, sort_keys=True)}"
        if details_dict
        else ""
    )
    return Message(
        role="system",
        content=(
            f"The previous {tool_name} tool call failed{code_suffix}: "
            f"{details}.{details_suffix} Use the tool schema and these structured "
            "error facts to choose the next action."
        ),
    )


def _build_intent_execution_state_message(
    loop_ctx: AdaptiveToolLoopContext,
) -> Message | None:
    state = getattr(loop_ctx, "state", None)
    if state is None:
        return None
    intent_execution_states = list(getattr(state, "intent_execution_states", []) or [])
    if not intent_execution_states:
        return None
    declared_count = max(
        len(list(getattr(state, "decision_sub_intent_refs", []) or [])),
        len(list(getattr(state, "decision_sub_intents", []) or [])),
        len(intent_execution_states),
    )
    max_items = max(1, min(5, declared_count))
    block = build_raw_intent_execution_state_block(
        intent_execution_states,
        max_items=max_items,
    )
    if not block:
        return None
    return Message(role="system", content=block)
