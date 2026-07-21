from typing import Any

from openminion.modules.brain.config import fixed_act_profile_from_profile
from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_ACT_PROFILE_GENERAL,
    BRAIN_ACT_PROFILE_ORCHESTRATE,
    BRAIN_ACT_PROFILE_RESEARCH,
    BRAIN_EXECUTION_TARGET_LOCAL,
    STATE_KEY_TASK_BACKED_RESUME,
)
from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.execution.child_tasks import (
    DecomposeControlPayload,
)
from openminion.modules.brain.schemas import (
    ActDecision,
    Decision,
    ExecutionTargetPayload,
    WorkingState,
)
from openminion.modules.llm.client_call import usage_payload_from_response_usage

from .entry import (
    ENTRY_CODING_TOOL_NAME,
    ENTRY_DECOMPOSE_TOOL_NAME,
    ENTRY_RESEARCH_TOOL_NAME,
    detect_entry_path,
)
from .failures import _internal_failure_answer


def _response_usage_payload(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    normalized = usage_payload_from_response_usage(usage)
    field_map = {
        "prompt_tokens": "input_tokens",
        "completion_tokens": "output_tokens",
        "total_tokens": "total_tokens",
        "total_source": "total_source",
        "cached_tokens": "cached_tokens",
        "cache_creation_tokens": "cache_creation_tokens",
    }
    return {
        output_key: normalized[source_key]
        for source_key, output_key in field_map.items()
        if source_key in normalized
    }


def _is_empty_entry_response(response: Any) -> bool:
    detection = detect_entry_path(response)
    if detection.path == "clarify":
        return not bool(detection.clarify_question)
    if detection.path == "act":
        return False
    return not bool(detection.response_text)


def _provisional_entry_route(
    *,
    runner: Any,
    state: WorkingState,
    has_new_user_input: bool,
) -> Any:
    from openminion.modules.brain.bootstrap.resolve import (  # noqa: PLC0415
        resolve_working_act_route,
    )

    bootstrap_decision = ActDecision(reason_code="entry_bootstrap")
    return resolve_working_act_route(
        decision=bootstrap_decision,
        state=state,
        default_act_profile=fixed_act_profile_from_profile(
            getattr(runner, "profile", None)
        ),
        has_new_user_input=has_new_user_input,
    )


def _should_bypass_unified_entry(
    *,
    route: Any,
    state: WorkingState,
) -> bool:
    execution_target_kind = str(
        getattr(getattr(route, "execution_target", None), "kind", "") or ""
    ).strip()
    if execution_target_kind == "delegated":
        return True
    act_profile = str(getattr(route, "act_profile", "") or "").strip().lower()
    if act_profile == "research":
        return True
    if act_profile == "orchestrate":
        return bool(
            getattr(state, STATE_KEY_TASK_BACKED_RESUME, {})
            or getattr(state, "child_task_order", [])
        )
    return False


def _bypass_decision_for_route(route: Any) -> Decision:
    decision = ActDecision(
        confidence=1.0,
        reason_code="bootstrap_resolved_workflow_entry_bypass",
    )
    decision._pre_resolved_act_route = route
    return decision


def _entry_tool_calls(response: Any, tool_name: str) -> list[Any]:
    return [
        call
        for call in list(getattr(response, "tool_calls", []) or [])
        if str(getattr(call, "name", "") or "").strip() == tool_name
    ]


_ENTRY_MUTATION_TO_CODING_TOOLS = frozenset({"file.write", "code.patch"})


def _entry_mutation_seed_should_route_to_coding(
    *,
    response: Any,
    provisional_route: Any,
) -> bool:
    if (
        str(getattr(provisional_route, "act_profile", "") or "").strip().lower()
        != BRAIN_ACT_PROFILE_GENERAL
    ):
        return False
    if str(getattr(provisional_route, "source", "") or "").strip() != (
        "runtime_default_general"
    ):
        return False
    tool_names = {
        str(getattr(call, "name", "") or "").strip()
        for call in list(getattr(response, "tool_calls", []) or [])
    }
    return bool(tool_names & _ENTRY_MUTATION_TO_CODING_TOOLS)


def _subtasks_from_decompose_payload(
    payload: DecomposeControlPayload,
) -> list[dict[str, Any]]:
    subtasks: list[dict[str, Any]] = []
    for item in payload.subtasks:
        subtasks.append(
            {
                "subtask_id": item.id,
                "goal": item.description,
                "inputs": dict(item.inputs),
                "depends_on": list(item.depends_on),
                "suggested_mode": item.suggested_mode,
                "priority": item.priority,
            }
        )
    return subtasks


def _local_route(*, act_profile: str, source: str) -> Any:
    from openminion.modules.brain.bootstrap.resolve import (  # noqa: PLC0415
        ResolvedActRoute,
    )

    return ResolvedActRoute(
        act_profile=act_profile,
        execution_target=ExecutionTargetPayload(kind=BRAIN_EXECUTION_TARGET_LOCAL),
        source=source,
    )


def _route_after_decompose_decline(provisional_route: Any) -> Any:
    act_profile = (
        str(getattr(provisional_route, "act_profile", "") or "").strip().lower()
    )
    if act_profile and act_profile != BRAIN_ACT_PROFILE_ORCHESTRATE:
        return provisional_route
    return _local_route(
        act_profile=BRAIN_ACT_PROFILE_GENERAL,
        source="entry_decompose_declined",
    )


def _entry_decompose_decision(
    *,
    response: Any,
    provisional_route: Any,
    logger: CanonicalEventLogger,
    state: WorkingState,
    llm_call_id: str,
    respond_decision_fn: Any,
) -> Decision | None:
    decompose_calls = _entry_tool_calls(response, ENTRY_DECOMPOSE_TOOL_NAME)
    if not decompose_calls:
        return None
    other_tool_names = [
        str(getattr(call, "name", "") or "").strip()
        for call in list(getattr(response, "tool_calls", []) or [])
        if str(getattr(call, "name", "") or "").strip() != ENTRY_DECOMPOSE_TOOL_NAME
    ]
    if other_tool_names:
        logger.emit(
            "brain.entry.decompose_invalid",
            {
                "llm_call_id": llm_call_id,
                "reason": "mixed_tool_calls",
                "other_tool_names": other_tool_names,
            },
            trace_id=state.trace_id,
            status="warning",
        )
        return respond_decision_fn(
            confidence=0.5,
            reason_code="entry_decompose_mixed_tool_calls",
            answer=_internal_failure_answer(detail="entry_decompose_mixed_tool_calls"),
        )

    try:
        payload = DecomposeControlPayload.model_validate(
            getattr(decompose_calls[0], "arguments", {}) or {}
        )
    except Exception as exc:  # noqa: BLE001
        logger.emit(
            "brain.entry.decompose_invalid",
            {
                "llm_call_id": llm_call_id,
                "reason": "invalid_payload",
                "error": str(exc),
            },
            trace_id=state.trace_id,
            status="warning",
        )
        return respond_decision_fn(
            confidence=0.5,
            reason_code="entry_decompose_invalid_payload",
            answer=_internal_failure_answer(detail="entry_decompose_invalid_payload"),
        )

    subtasks = _subtasks_from_decompose_payload(payload)
    if not subtasks:
        logger.emit(
            "brain.entry.decompose_declined",
            {"llm_call_id": llm_call_id, "subtask_count": 0},
            trace_id=state.trace_id,
        )
        decision = ActDecision(
            confidence=0.5,
            reason_code="entry_decompose_declined",
        )
        decision._pre_resolved_act_route = _route_after_decompose_decline(
            provisional_route
        )
        return decision

    logger.emit(
        "brain.entry.decompose_routed",
        {"llm_call_id": llm_call_id, "subtask_count": len(subtasks)},
        trace_id=state.trace_id,
    )
    decision = ActDecision(
        confidence=0.5,
        reason_code="entry_decompose_tool_call",
        act_profile=BRAIN_ACT_PROFILE_ORCHESTRATE,
        subtasks=subtasks,
    )
    decision._pre_resolved_act_route = _local_route(
        act_profile=BRAIN_ACT_PROFILE_ORCHESTRATE,
        source="entry_decompose_tool_call",
    )
    return decision


_REPLAY_CONTINUATION_REASON_CODES = {
    "confirmation_replay",
    "confirmation_replay_validation",
    "plan_continuation_after_deny",
}


def _entry_query_text(*, state: WorkingState, user_input: str | None) -> str:
    explicit_user_input = str(user_input or "").strip()
    if explicit_user_input:
        return explicit_user_input

    reason_code = str(getattr(state, "decision_reason_code", "") or "").strip().lower()
    if reason_code in _REPLAY_CONTINUATION_REASON_CODES:
        continuation_guidance = str(
            getattr(state, "post_action_user_message", "") or ""
        ).strip()
        if continuation_guidance:
            return continuation_guidance
        original_query = (
            str(getattr(state, "goal", "") or "").strip()
            or str(getattr(state, "last_user_input", "") or "").strip()
            or str(
                getattr(state, "pending_confirmation_last_user_input", "") or ""
            ).strip()
        )
        if original_query:
            return (
                "Continue from the current confirmed task state. Do not restart "
                "from the original request. Use the existing workspace and recent "
                f"tool results to finish the task. Continue original task: {original_query}"
            )
        return (
            "Continue from the current confirmed task state. Do not restart from "
            "the original request. Use the existing workspace and recent tool "
            "results to finish the task."
        )

    return str(getattr(state, "goal", "") or "").strip()


def _entry_research_decision(
    *,
    response: Any,
    logger: CanonicalEventLogger,
    state: WorkingState,
    llm_call_id: str,
    respond_decision_fn: Any,
) -> Decision | None:
    research_calls = _entry_tool_calls(response, ENTRY_RESEARCH_TOOL_NAME)
    if not research_calls:
        return None
    other_tool_names = [
        str(getattr(call, "name", "") or "").strip()
        for call in list(getattr(response, "tool_calls", []) or [])
        if str(getattr(call, "name", "") or "").strip() != ENTRY_RESEARCH_TOOL_NAME
    ]
    if other_tool_names:
        logger.emit(
            "brain.entry.research_invalid",
            {
                "llm_call_id": llm_call_id,
                "reason": "mixed_tool_calls",
                "other_tool_names": other_tool_names,
            },
            trace_id=state.trace_id,
            status="warning",
        )
        return respond_decision_fn(
            confidence=0.5,
            reason_code="entry_research_mixed_tool_calls",
            answer=_internal_failure_answer(detail="entry_research_mixed_tool_calls"),
        )

    logger.emit(
        "brain.entry.research_routed",
        {"llm_call_id": llm_call_id},
        trace_id=state.trace_id,
    )
    decision = ActDecision(
        confidence=0.5,
        reason_code="entry_research_tool_call",
        act_profile=BRAIN_ACT_PROFILE_RESEARCH,
    )
    decision._pre_resolved_act_route = _local_route(
        act_profile=BRAIN_ACT_PROFILE_RESEARCH,
        source="entry_research_tool_call",
    )
    return decision


def _entry_coding_decision(
    *,
    response: Any,
    logger: CanonicalEventLogger,
    state: WorkingState,
    llm_call_id: str,
    respond_decision_fn: Any,
) -> Decision | None:
    coding_calls = _entry_tool_calls(response, ENTRY_CODING_TOOL_NAME)
    if not coding_calls:
        return None
    other_tool_names = [
        str(getattr(call, "name", "") or "").strip()
        for call in list(getattr(response, "tool_calls", []) or [])
        if str(getattr(call, "name", "") or "").strip() != ENTRY_CODING_TOOL_NAME
    ]
    if other_tool_names:
        logger.emit(
            "brain.entry.coding_invalid",
            {
                "llm_call_id": llm_call_id,
                "reason": "mixed_tool_calls",
                "other_tool_names": other_tool_names,
            },
            trace_id=state.trace_id,
            status="warning",
        )
        return respond_decision_fn(
            confidence=0.5,
            reason_code="entry_coding_mixed_tool_calls",
            answer=_internal_failure_answer(detail="entry_coding_mixed_tool_calls"),
        )

    logger.emit(
        "brain.entry.coding_routed",
        {"llm_call_id": llm_call_id},
        trace_id=state.trace_id,
    )
    decision = ActDecision(
        confidence=0.5,
        reason_code="entry_coding_tool_call",
        act_profile=BRAIN_ACT_PROFILE_CODING,
    )
    decision._pre_resolved_act_route = _local_route(
        act_profile=BRAIN_ACT_PROFILE_CODING,
        source="entry_coding_tool_call",
    )
    return decision
