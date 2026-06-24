from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from openminion.modules.brain.schemas import (
    ConfidentComplete,
    DelegationContext,
    DelegationResultSummary,
    FinalizationStatus,
    GoalDeclaration,
    GoalRevision,
    MetaRulePreference,
    MemoryConsolidationResult,
    PendingTurnContext,
    SessionWorkSummary,
    WatchOutcome,
)
from openminion.modules.context.schemas import (
    TaskPlan,
    TaskPlanRevision,
    TaskPlanStepBlocked,
    TaskPlanStepCompleted,
    TaskPlanTerminalSignal,
)

from .contracts import AdaptiveToolLoopState
from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS

ModelT = TypeVar("ModelT", bound=BaseModel)


_CONFIDENT_COMPLETE_GUIDANCE = (
    "When you are confident the task is complete and no further tool calls are "
    "needed, provide the full user-facing final answer and populate the structured "
    "confident_complete signal with complete=true and reasoning. Do not set the "
    "signal unless the final answer text is already present, and do not set it if "
    "you still need tools."
)
_FINALIZATION_STATUS_GUIDANCE = (
    "When ending an act turn that requires finalization, populate the structured "
    "finalization_status signal before ending the turn with final text. Use "
    "status=final_answer only when the final deliverable is already present. Use "
    "status=incomplete when more synthesis or follow-up work would still be "
    "required. Use status=blocked when a concrete blocker prevented completion. "
    "Always include the user-facing answer text before the finalization_status "
    "signal. Preserve any user-specified final-answer format, headings, section "
    "titles, ordering, or exact-response constraints; do not replace a requested "
    "format with a generic completion summary."
)
_FINALIZATION_STATUS_SALVAGE_GUIDANCE = (
    "You already produced the full user-facing answer above. Do not repeat or "
    "expand it. Return only the structured finalization_status signal now. Do "
    "not call tools. Set status=final_answer only if the prior answer fully "
    "completed the request. Use status=incomplete or status=blocked otherwise."
)
_PENDING_TURN_CONTEXT_GUIDANCE = (
    "When your final user-facing answer offers, proposes, or suggests a concrete "
    "next action that the user could accept with a short reply like yes, sure, or "
    "do it, populate the structured pending_turn_context signal. Use it to carry "
    "forward the original request, active work summary, known context, missing "
    "fields, artifact refs, and response preferences so the next turn can resolve "
    "short follow-ups without losing the thread. Do not set it if you still need "
    "tools."
)
_SESSION_WORK_SUMMARY_GUIDANCE = (
    "After significant work milestones, you may populate the structured "
    "session_work_summary signal. Keep the summary concise, within 800 characters, "
    "and only set it when the checkpoint materially helps future turns. Do not set "
    "it if you still need tools."
)
_META_RULE_PREFERENCE_GUIDANCE = (
    "When you learn a reusable retry, replan, or budget threshold that should guide "
    "future sessions, populate the structured meta_rule_preference signal with "
    "rule, preferred_value, and reasoning. Use only thresholds or bounded policy "
    "preferences you want recalled later. Do not set it if you still need tools."
)
_GOAL_DECLARATION_GUIDANCE = (
    "When observed context (recalled memory, tool-outcome patterns, conversation "
    "cues) makes you confident the agent should proactively start, monitor, or "
    "schedule work the user has not explicitly requested, populate the structured "
    "goal_declaration signal with goal (what), trigger (why now), priority, and "
    "action_type ('watch' / 'task' / 'suggest' / 'none'). Set this rarely — only "
    "when the trigger is concrete; do not declare a goal on every turn. Do not "
    "set it if you still need tools."
)
_GOAL_REVISION_GUIDANCE = (
    "When you previously declared a goal and observed counter-evidence or changed "
    "context now justifies revising it, populate the structured goal_revision "
    "signal with previous_goal, goal, trigger, priority, and action_type "
    "('watch' / 'task' / 'suggest' / 'none'). Only set this when the prior goal "
    "is concrete; do not set it if you still need tools."
)
_WATCH_OUTCOME_GUIDANCE = (
    "For watch-check turns, after your final user-facing answer, populate the "
    "structured watch_outcome signal with condition_met and summary. Set "
    "condition_met=true only when the watch alert condition is satisfied. Do not "
    "set it if you still need tools."
)
_WATCH_ACTION_GUIDANCE = (
    "For watch-triggered action turns, execute the declared follow-up action using "
    "only background-safe tools. Confirmation-required tools fail closed in these "
    "background runs, so prefer auto-allowed tools and finish with the normal "
    "user-facing final answer only. Do not set watch_outcome on action turns."
)
_MEMORY_CONSOLIDATION_GUIDANCE = (
    "For memory-consolidation turns, review the provided candidate batch and decide "
    "which candidates to promote, discard, or defer. After your brief final summary, "
    "populate the structured memory_consolidation signal with decisions containing "
    "candidate_id, action, and reasoning. Only use promote, discard, or defer. Do "
    "not set it if you still need tools."
)
_DELEGATION_RESULT_SUMMARY_GUIDANCE = (
    "When this turn is delegated work from another agent, answer the delegated task "
    "normally and populate the structured delegation_result_summary signal with "
    "summary, artifacts_produced, and status. Keep the summary within 800 "
    "characters and only report artifacts you actually produced or used."
)
_TASK_PLAN_GUIDANCE = (
    "For complex multi-step user work, record a model-authored task plan by "
    'calling the plan loop-control tool with action="declare". Include plan_id, '
    "objective, and steps. Each step includes step_id, description, depends_on, "
    "estimated_difficulty, and bounded tool_families. Only use these "
    "tool_families: browser, code, exec, fetch, file, ip, location, search, "
    "skill, task, time, utility, weather, web. Use at most one active plan per "
    "session; a new declaration replaces the prior active plan."
)
_TASK_PLAN_PROGRESS_GUIDANCE = (
    "When updating an active task plan, call the plan tool with action "
    "step_completed, step_blocked, revise, abandon, or complete. Keep "
    "step_completed.output_summary concise; the runtime transports your text "
    "but does not interpret step content. Do not record step progress unless "
    "an active plan already exists or you also declared the plan earlier in "
    "this same turn."
)


def _validated_payload(
    response: Any,
    *,
    field_name: str,
    model: type[ModelT],
) -> ModelT | None:
    payload = getattr(response, field_name, None)
    if not isinstance(payload, dict):
        return None
    try:
        return model.model_validate(payload)
    except ValidationError:
        return None


def _confident_complete_payload(response: Any) -> ConfidentComplete | None:
    return _validated_payload(
        response,
        field_name="confident_complete",
        model=ConfidentComplete,
    )


def _finalization_status_payload(response: Any) -> FinalizationStatus | None:
    return _validated_payload(
        response,
        field_name=STATE_KEY_FINALIZATION_STATUS,
        model=FinalizationStatus,
    )


def _pending_finalization_salvage_text(
    loop_state: AdaptiveToolLoopState,
) -> str | None:
    text = loop_state.scratchpad.get("typed_finalization_status_salvage_text")
    rendered = str(text or "").strip()
    return rendered or None


def _watch_outcome_payload(response: Any) -> WatchOutcome | None:
    return _validated_payload(
        response,
        field_name="watch_outcome",
        model=WatchOutcome,
    )


def _pending_turn_context_payload(response: Any) -> PendingTurnContext | None:
    return _validated_payload(
        response,
        field_name="pending_turn_context",
        model=PendingTurnContext,
    )


def _meta_rule_preference_payload(response: Any) -> MetaRulePreference | None:
    return _validated_payload(
        response,
        field_name="meta_rule_preference",
        model=MetaRulePreference,
    )


def _memory_consolidation_payload(response: Any) -> MemoryConsolidationResult | None:
    return _validated_payload(
        response,
        field_name="memory_consolidation",
        model=MemoryConsolidationResult,
    )


def _session_work_summary_payload(response: Any) -> SessionWorkSummary | None:
    return _validated_payload(
        response,
        field_name="session_work_summary",
        model=SessionWorkSummary,
    )


def _goal_declaration_payload(response: Any) -> GoalDeclaration | None:
    return _validated_payload(
        response,
        field_name="goal_declaration",
        model=GoalDeclaration,
    )


def _goal_revision_payload(response: Any) -> GoalRevision | None:
    return _validated_payload(
        response,
        field_name="goal_revision",
        model=GoalRevision,
    )


def _task_plan_payload(response: Any) -> TaskPlan | None:
    return _validated_payload(
        response,
        field_name="task_plan",
        model=TaskPlan,
    )


def _task_plan_revision_payload(response: Any) -> TaskPlanRevision | None:
    return _validated_payload(
        response,
        field_name="task_plan_revision",
        model=TaskPlanRevision,
    )


def _task_plan_step_completed_payload(
    response: Any,
) -> TaskPlanStepCompleted | None:
    return _validated_payload(
        response,
        field_name="task_plan_step_completed",
        model=TaskPlanStepCompleted,
    )


def _task_plan_step_blocked_payload(response: Any) -> TaskPlanStepBlocked | None:
    return _validated_payload(
        response,
        field_name="task_plan_step_blocked",
        model=TaskPlanStepBlocked,
    )


def _task_plan_abandoned_payload(response: Any) -> TaskPlanTerminalSignal | None:
    return _validated_payload(
        response,
        field_name="task_plan_abandoned",
        model=TaskPlanTerminalSignal,
    )


def _task_plan_completed_payload(response: Any) -> TaskPlanTerminalSignal | None:
    return _validated_payload(
        response,
        field_name="task_plan_completed",
        model=TaskPlanTerminalSignal,
    )


def _delegation_context_payload(response: Any) -> DelegationContext | None:
    return _validated_payload(
        response,
        field_name="delegation_context",
        model=DelegationContext,
    )


def _delegation_result_summary_payload(
    response: Any,
) -> DelegationResultSummary | None:
    return _validated_payload(
        response,
        field_name="delegation_result_summary",
        model=DelegationResultSummary,
    )
