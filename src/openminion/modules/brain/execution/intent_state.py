import json
from typing import Any

from openminion.modules.prompting.continuation import (
    PARTIAL_SUCCESS_CONTINUATION_PROMPT,
)

from ..constants import (
    BRAIN_ACTION_STATUS_BLOCKED,
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_ACTION_STATUS_RETRY,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_ACTION_STATUS_TIMEOUT,
    BRAIN_DECISION_ROUTE_ACT,
    BRAIN_EXECUTION_OUTCOME_BLOCKED,
    BRAIN_EXECUTION_OUTCOME_FAILED,
    BRAIN_EXECUTION_OUTCOME_IN_PROGRESS,
    BRAIN_EXECUTION_OUTCOME_NEEDS_USER,
    BRAIN_EXECUTION_OUTCOME_PENDING,
    BRAIN_EXECUTION_OUTCOME_RETRYING,
    BRAIN_EXECUTION_OUTCOME_SKIPPED,
    BRAIN_EXECUTION_OUTCOME_SUCCEEDED,
)
from ..schemas import (
    ActionResult,
    Decision,
    IntentExecutionState,
    Plan,
    WorkingState,
    build_intent_execution_states,
    iso_now,
    normalize_sub_intent_ids,
    to_structured_sub_intents,
)

_INTENT_COMPLETED_STATUSES = {"succeeded"}
_RAW_INTENT_EXECUTION_STATE_MAX_ITEMS = 5
_CONTINUATION_DECISION_REASON_CODES = {
    "resume_existing_plan",
    "confirmation_replay",
    "plan_continuation_after_deny",
}


def succeeded_intent_ids(
    intent_execution_states: list[IntentExecutionState] | None,
) -> list[str]:
    return [
        str(item.intent_id or "").strip()
        for item in list(intent_execution_states or [])
        if str(getattr(item, "status", "") or "").strip() in _INTENT_COMPLETED_STATUSES
        and str(item.intent_id or "").strip()
    ]


def remaining_intent_ids(
    intent_execution_states: list[IntentExecutionState] | None,
) -> list[str]:
    return [
        str(item.intent_id or "").strip()
        for item in list(intent_execution_states or [])
        if str(getattr(item, "status", "") or "").strip()
        not in _INTENT_COMPLETED_STATUSES
        and str(item.intent_id or "").strip()
    ]


def _intent_status_reason(item: IntentExecutionState) -> str:
    summary = str(getattr(item, "summary", "") or "").strip()
    if summary:
        return summary
    status = str(getattr(item, "status", "") or "").strip()
    if status == BRAIN_EXECUTION_OUTCOME_FAILED:
        return "failed before completion"
    if status == BRAIN_EXECUTION_OUTCOME_BLOCKED:
        return "blocked before completion"
    if status == BRAIN_EXECUTION_OUTCOME_NEEDS_USER:
        return "needs your input"
    if status == BRAIN_EXECUTION_OUTCOME_SKIPPED:
        return "skipped"
    if status == BRAIN_EXECUTION_OUTCOME_RETRYING:
        return "retrying when this turn ended"
    if status == BRAIN_EXECUTION_OUTCOME_IN_PROGRESS:
        return "still in progress when this turn ended"
    if status == BRAIN_EXECUTION_OUTCOME_PENDING:
        return "not reached before this turn ended"
    return status or "not completed"


def build_partial_success_summary(
    intent_execution_states: list[IntentExecutionState] | None,
    *,
    continuation_prompt: str | None = PARTIAL_SUCCESS_CONTINUATION_PROMPT,
) -> str | None:
    states = list(intent_execution_states or [])
    if not states:
        return None
    completed = [
        item
        for item in states
        if str(getattr(item, "status", "") or "").strip() in _INTENT_COMPLETED_STATUSES
    ]
    incomplete = [
        item
        for item in states
        if str(getattr(item, "status", "") or "").strip()
        not in _INTENT_COMPLETED_STATUSES
    ]
    if not incomplete:
        return None

    lines: list[str] = []
    if completed:
        lines.append("Completed:")
        for item in completed:
            detail = str(getattr(item, "summary", "") or "").strip()
            description = (
                str(getattr(item, "description", "") or "").strip()
                or str(getattr(item, "intent_id", "") or "").strip()
                or "Unnamed intent"
            )
            if detail:
                lines.append(f"- [done] {description} - {detail}")
            else:
                lines.append(f"- [done] {description}")
        lines.append("")

    lines.append("Not completed:")
    for item in incomplete:
        description = (
            str(getattr(item, "description", "") or "").strip()
            or str(getattr(item, "intent_id", "") or "").strip()
            or "Unnamed intent"
        )
        lines.append(f"- [pending] {description} - {_intent_status_reason(item)}")

    prompt = str(continuation_prompt or "").strip()
    if prompt:
        lines.append("")
        lines.append(prompt)
    return "\n".join(lines)


def build_raw_intent_execution_state_block(
    intent_execution_states: list[IntentExecutionState] | None,
    *,
    max_items: int = _RAW_INTENT_EXECUTION_STATE_MAX_ITEMS,
) -> str | None:
    states = list(intent_execution_states or [])
    if not states:
        return None
    bounded_max_items = max(1, int(max_items or _RAW_INTENT_EXECUTION_STATE_MAX_ITEMS))
    payload: list[dict[str, Any]] = []
    for item in states[:bounded_max_items]:
        payload.append(
            {
                "intent_id": str(getattr(item, "intent_id", "") or "").strip(),
                "status": str(getattr(item, "status", "") or "").strip(),
                "skill_id": str(getattr(item, "skill_id", "") or "").strip() or None,
                "depends_on": [
                    str(value).strip()
                    for value in list(getattr(item, "depends_on", []) or [])
                    if str(value).strip()
                ],
                "last_step_index": getattr(item, "last_step_index", None),
                "updated_at": str(getattr(item, "updated_at", "") or "").strip()
                or None,
            }
        )
    if not payload:
        return None
    return "intent_execution_states=" + json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def structured_sub_intents_for_decision(decision: Decision | None) -> list[Any]:
    if decision is None or not getattr(decision, "sub_intents", None):
        return []
    return to_structured_sub_intents(getattr(decision, "sub_intents", []) or [])


def attach_command_sub_intent_refs(
    *,
    command: Any,
    structured_sub_intents: list[Any],
) -> Any:
    if command is None or not structured_sub_intents:
        return command
    allowed_ids = [item.id for item in structured_sub_intents]
    sub_intent_ids = normalize_sub_intent_ids(
        getattr(command, "sub_intent_ids", []),
        allowed_ids=allowed_ids,
    )
    if not sub_intent_ids:
        sub_intent_ids = list(allowed_ids)
    if sub_intent_ids == list(getattr(command, "sub_intent_ids", []) or []):
        return command
    return command.model_copy(
        update={"sub_intent_ids": sub_intent_ids},
        deep=True,
    )


def clear_working_route(*, state: Any) -> None:
    state.working_act_profile = None
    state.working_execution_target_kind = None
    state.working_route_source = None


def record_working_route(
    *,
    state: Any,
    act_profile: str | None,
    execution_target_kind: str | None,
    source: str | None,
) -> None:
    state.working_act_profile = str(act_profile or "").strip().lower() or None
    state.working_execution_target_kind = (
        str(execution_target_kind or "").strip().lower() or None
    )
    state.working_route_source = str(source or "").strip() or None


def record_decision_metadata(
    *,
    state: Any,
    decision: Decision | None,
    plan: Plan | None,
    capability_category: str | None = None,
) -> None:
    if decision is None:
        state.decision_reason_code = ""
        state.decision_capability_category = None
        state.decision_sub_intents = []
        state.decision_sub_intent_refs = []
        state.decision_rationale = ""
        state.decision_success_criteria = {}
        state.decision_feasibility_state = {}
        state.decision_feasibility_report = None
        state.request_readiness = None
        state.adaptive_satisfied_intent_ids = []
        state.last_adaptive_revision_checkpoint = None
        state.intent_execution_states = []
        return
    next_sub_intents = list(getattr(decision, "sub_intents", []) or [])
    next_sub_intent_refs = structured_sub_intents_for_decision(decision)
    next_rationale = str(getattr(decision, "rationale", "") or "").strip()
    decision_reason = str(getattr(decision, "reason_code", "") or "").strip()
    is_continuation_reason = decision_reason in _CONTINUATION_DECISION_REASON_CODES
    state.decision_reason_code = decision_reason
    request_readiness = getattr(decision, "request_readiness", None)
    state.request_readiness = (
        request_readiness.model_copy(deep=True)
        if request_readiness is not None
        else None
    )
    state.decision_capability_category = (
        str(capability_category or "").strip().lower() or None
    )

    if next_sub_intents:
        state.decision_sub_intents = next_sub_intents
    elif not is_continuation_reason:
        state.decision_sub_intents = []
    if next_sub_intent_refs:
        state.decision_sub_intent_refs = next_sub_intent_refs
    elif plan is not None and getattr(plan, "sub_intents", None):
        state.decision_sub_intent_refs = list(getattr(plan, "sub_intents", []) or [])
    elif not is_continuation_reason:
        state.decision_sub_intent_refs = []

    if next_rationale:
        state.decision_rationale = next_rationale
    elif not is_continuation_reason:
        state.decision_rationale = ""
    if not is_continuation_reason:
        state.decision_feasibility_state = {}
        state.decision_feasibility_report = None
        state.adaptive_satisfied_intent_ids = []
        state.last_adaptive_revision_checkpoint = None
    if plan is not None and isinstance(getattr(plan, "success_criteria", None), dict):
        state.decision_success_criteria = dict(plan.success_criteria)
    elif getattr(decision, "route", "") == BRAIN_DECISION_ROUTE_ACT:
        seeded_commands = list(getattr(decision, "_seeded_commands", []) or [])
        seeded_command = seeded_commands[0] if seeded_commands else None
        if isinstance(getattr(seeded_command, "success_criteria", None), dict):
            state.decision_success_criteria = dict(seeded_command.success_criteria)
        else:
            state.decision_success_criteria = {}
    else:
        state.decision_success_criteria = {}

    structured_source = list(getattr(state, "decision_sub_intent_refs", []) or [])
    if structured_source:
        state.intent_execution_states = build_intent_execution_states(
            structured_source,
            existing=getattr(state, "intent_execution_states", []) or [],
        )
    else:
        state.intent_execution_states = []


def _plan_steps_for_intent(plan: Plan | None, *, intent_id: str) -> list[int]:
    if plan is None:
        return []
    matches: list[int] = []
    for index, step in enumerate(plan.steps):
        if intent_id in normalize_sub_intent_ids(getattr(step, "sub_intent_ids", [])):
            matches.append(index)
    return matches


def _outcome_for_success(
    *,
    state: WorkingState,
    current_step_index: int,
    intent_id: str,
) -> str:
    future_steps = [
        index
        for index in _plan_steps_for_intent(
            getattr(state, "plan", None), intent_id=intent_id
        )
        if index > current_step_index
    ]
    return (
        BRAIN_EXECUTION_OUTCOME_IN_PROGRESS
        if future_steps
        else BRAIN_EXECUTION_OUTCOME_SUCCEEDED
    )


def _resolve_execution_outcome(
    *,
    runner: Any,
    state: WorkingState,
    action_result: ActionResult,
    current_step_index: int,
    intent_id: str,
) -> str:
    status = str(action_result.status or "").strip().lower()
    if status == BRAIN_ACTION_STATUS_SUCCESS:
        return _outcome_for_success(
            state=state,
            current_step_index=current_step_index,
            intent_id=intent_id,
        )
    if status == BRAIN_ACTION_STATUS_RETRY:
        return BRAIN_EXECUTION_OUTCOME_RETRYING
    if status == BRAIN_ACTION_STATUS_BLOCKED:
        return BRAIN_EXECUTION_OUTCOME_BLOCKED
    if status == BRAIN_ACTION_STATUS_NEEDS_USER:
        return BRAIN_EXECUTION_OUTCOME_NEEDS_USER
    if (
        status == BRAIN_ACTION_STATUS_FAILED
        and runner.options.failure_strategy == "skip"
    ):
        future_steps = [
            index
            for index in _plan_steps_for_intent(
                getattr(state, "plan", None),
                intent_id=intent_id,
            )
            if index > current_step_index
        ]
        return (
            BRAIN_EXECUTION_OUTCOME_IN_PROGRESS
            if future_steps
            else BRAIN_EXECUTION_OUTCOME_SKIPPED
        )
    if status in {BRAIN_ACTION_STATUS_FAILED, BRAIN_ACTION_STATUS_TIMEOUT}:
        return BRAIN_EXECUTION_OUTCOME_FAILED
    return BRAIN_EXECUTION_OUTCOME_PENDING


def update_intent_execution_states(
    runner: Any,
    *,
    state: WorkingState,
    command: Any | None,
    action_result: ActionResult,
    current_step_index: int,
) -> None:
    if command is None or not state.intent_execution_states:
        return
    intent_ids = normalize_sub_intent_ids(getattr(command, "sub_intent_ids", []))
    if not intent_ids:
        return
    updated_states: list[IntentExecutionState] = []
    for item in state.intent_execution_states:
        if item.intent_id not in intent_ids:
            updated_states.append(item)
            continue
        updated_states.append(
            item.model_copy(
                update={
                    "status": _resolve_execution_outcome(
                        runner=runner,
                        state=state,
                        action_result=action_result,
                        current_step_index=current_step_index,
                        intent_id=item.intent_id,
                    ),
                    "last_command_id": str(action_result.command_id or "").strip(),
                    "last_step_index": current_step_index,
                    "last_action_status": str(action_result.status or "").strip(),
                    "summary": str(action_result.summary or "").strip(),
                    "updated_at": iso_now(),
                },
                deep=True,
            )
        )
    state.intent_execution_states = updated_states


__all__ = [
    "attach_command_sub_intent_refs",
    "build_partial_success_summary",
    "record_decision_metadata",
    "remaining_intent_ids",
    "succeeded_intent_ids",
    "structured_sub_intents_for_decision",
    "update_intent_execution_states",
]
