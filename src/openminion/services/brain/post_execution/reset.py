from typing import Any

from openminion.modules.brain.loop.context.pending_turn import (
    preserve_pending_turn_context_on_new_input,
)
from openminion.modules.brain.execution.mission import (
    llm_calls_max_from_runner,
    mission_enabled,
    mission_is_active,
    reset_policy_for,
    resolve_mission_input_route,
    set_mission_status,
    update_mission_objective,
    update_mission_task,
)
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.runner.tick.context import (
    _parse_confirmation_response,
)
from openminion.modules.brain.schemas import WorkingState

from .contracts import _MissionResetPreview, _TurnResetPreservation
from openminion.modules.brain.constants import STATE_KEY_TASK_BACKED_RESUME

# BBPC: telemetry event name emitted by `_reset_state_for_new_input(...)`
BRAIN_BRIDGE_CONFIRMATION_RESET_PRESERVED_EVENT = (
    "brain.bridge.confirmation_reset_preserved"
)

_RESUME_LIKE_INPUTS = frozenset(
    {
        "resume",
        "continue",
        "continue plan",
        "continue previous plan",
        "continue with previous plan",
    }
)
# Resume-control vocabulary only. Policy-confirmation tokens
_FOLLOWUP_CONTROL_INPUTS = _RESUME_LIKE_INPUTS.union(
    {"retry", "retry plan", "skip", "cancel"}
)
# BBPC: parser verdicts that should preserve a pending confirmation
_CONFIRMATION_PRESERVING_REPLIES = frozenset({"affirm", "deny"})
_CONTINUATION_PROGRESS_CONSTRAINT_PREFIX = "CLOSURE_CONTINUE_PROGRESS:"
_EXECUTABLE_CURRENT_STEP_KINDS = frozenset({"tool", "agent", "think", "finish"})
_RESUMABLE_WAITING_PHASES = frozenset(
    {"PLAN", "APPROVE", "ACT", "OBSERVE", "REFLECT", "IMPROVE", "VERIFY"}
)


def _latest_working_state_inline(
    *,
    runner: BrainRunner,
    session_id: str,
) -> dict[str, Any] | None:
    try:
        raw = runner.session_api.get_latest_working_state(session_id)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(raw, dict):
        return None
    state_inline = (
        raw.get("state_inline") if isinstance(raw.get("state_inline"), dict) else raw
    )
    return state_inline if isinstance(state_inline, dict) else None


def _write_working_state_inline(
    *,
    runner: BrainRunner,
    session_id: str,
    state_inline: dict[str, Any],
) -> bool:
    try:
        runner.session_api.put_working_state(session_id, state_inline=state_inline)
    except Exception:  # noqa: BLE001
        return False
    return True


def _mission_reset_preview(
    *,
    runner: BrainRunner,
    state_inline: dict[str, Any],
    user_input: str,
) -> _MissionResetPreview:
    parsed_state: WorkingState | None = None
    try:
        parsed_state = WorkingState.model_validate(state_inline)
    except Exception:  # noqa: BLE001
        parsed_state = None
    mission_runtime_active = bool(parsed_state is not None and mission_enabled(runner))
    route_action = "ordinary"
    route_objective = ""
    route_fork_input = ""
    if parsed_state is not None and mission_runtime_active:
        mission_route = resolve_mission_input_route(
            state=parsed_state,
            user_input=user_input,
        )
        route_action = str(mission_route.action or "ordinary").strip()
        route_objective = str(getattr(mission_route, "objective", "") or "").strip()
        route_fork_input = str(
            getattr(mission_route, "ordinary_input", "") or ""
        ).strip()
    return _MissionResetPreview(
        parsed_state=parsed_state,
        mission_runtime_active=mission_runtime_active,
        route_action=route_action,
        route_objective=route_objective,
        route_fork_input=route_fork_input,
    )


def _turn_reset_preservation(
    *,
    state_inline: dict[str, Any],
    user_input: str,
    parsed_confirmation_reply: str = "",
) -> _TurnResetPreservation:
    previous_goal = str(state_inline.get("goal", "") or "").strip()
    normalized_current_input = " ".join(str(user_input or "").strip().lower().split())
    plan_steps, cursor, has_executable_current_step = _reset_plan_position(
        state_inline=state_inline
    )
    phase = str(state_inline.get("phase", "") or "").strip().upper()
    pending_confirmation_command = state_inline.get("pending_confirmation_command")
    decision_feasibility_state = _decision_feasibility_state(state_inline=state_inline)
    has_pending_continuation_reply = bool(
        state_inline.get("awaiting_continuation_reply")
    )
    has_resumable_plan = _has_resumable_plan(
        state_inline=state_inline,
        plan_steps=plan_steps,
        cursor=cursor,
        phase=phase,
        pending_confirmation_command=pending_confirmation_command,
        decision_feasibility_state=decision_feasibility_state,
        has_executable_current_step=has_executable_current_step,
    )
    preserve_existing_plan = _preserve_existing_plan(
        normalized_current_input=normalized_current_input,
        has_resumable_plan=has_resumable_plan,
        decision_feasibility_state=decision_feasibility_state,
    )
    preserve_decision_state = _preserve_decision_state(
        normalized_current_input=normalized_current_input,
        has_resumable_plan=has_resumable_plan,
        has_pending_continuation_reply=has_pending_continuation_reply,
    )
    normalized_parsed_reply = str(parsed_confirmation_reply or "").strip().lower()
    preserve_followup_goal = bool(
        normalized_current_input in _FOLLOWUP_CONTROL_INPUTS and previous_goal
    )
    preserve_continuation_guard = bool(
        normalized_current_input in _FOLLOWUP_CONTROL_INPUTS
        and str(
            state_inline.get("continuation_guard_command_signature", "") or ""
        ).strip()
    )
    preserve_continuation_reply = bool(
        has_pending_continuation_reply
        and normalized_current_input in _FOLLOWUP_CONTROL_INPUTS
    )
    preserve_pending_confirmation = bool(
        pending_confirmation_command is not None
        and (
            normalized_current_input in _FOLLOWUP_CONTROL_INPUTS
            or normalized_parsed_reply in _CONFIRMATION_PRESERVING_REPLIES
        )
    )
    return _TurnResetPreservation(
        previous_goal=previous_goal,
        normalized_current_input=normalized_current_input,
        pending_confirmation_command=pending_confirmation_command,
        decision_feasibility_state=(
            dict(decision_feasibility_state)
            if isinstance(decision_feasibility_state, dict)
            else {}
        ),
        preserve_existing_plan=preserve_existing_plan,
        preserve_followup_goal=preserve_followup_goal,
        preserve_decision_state=preserve_decision_state,
        preserve_continuation_guard=preserve_continuation_guard,
        preserve_continuation_reply=preserve_continuation_reply,
        preserve_pending_confirmation=preserve_pending_confirmation,
        continuation_constraints=[
            str(item)
            for item in list(state_inline.get("constraints", []) or [])
            if str(item or "").startswith(_CONTINUATION_PROGRESS_CONSTRAINT_PREFIX)
        ],
        parsed_confirmation_reply=normalized_parsed_reply,
    )


def _reset_plan_position(
    *,
    state_inline: dict[str, Any],
) -> tuple[list[Any], int, bool]:
    plan_payload = state_inline.get("plan")
    plan_steps = (
        list(plan_payload.get("steps", []))
        if isinstance(plan_payload, dict)
        and isinstance(plan_payload.get("steps"), list)
        else []
    )
    try:
        cursor = int(state_inline.get("cursor", 0) or 0)
    except Exception:  # noqa: BLE001
        cursor = 0
    current_step = plan_steps[cursor] if 0 <= cursor < len(plan_steps) else {}
    current_step_kind = (
        str(current_step.get("kind", "") or "").strip().lower()
        if isinstance(current_step, dict)
        else ""
    )
    return plan_steps, cursor, current_step_kind in _EXECUTABLE_CURRENT_STEP_KINDS


def _decision_feasibility_state(
    *,
    state_inline: dict[str, Any],
) -> dict[str, Any]:
    value = state_inline.get("decision_feasibility_state")
    return value if isinstance(value, dict) else {}


def _has_resumable_plan(
    *,
    state_inline: dict[str, Any],
    plan_steps: list[Any],
    cursor: int,
    phase: str,
    pending_confirmation_command: Any,
    decision_feasibility_state: dict[str, Any],
    has_executable_current_step: bool,
) -> bool:
    status = str(state_inline.get("status", "")).strip().lower()
    return bool(
        plan_steps
        and (
            pending_confirmation_command is not None
            or decision_feasibility_state.get("awaiting_user_choice")
            or status == "active"
            or (status == "waiting_user" and has_executable_current_step)
            or (status == "waiting_user" and 0 < cursor < len(plan_steps))
            or (status == "waiting_user" and phase in _RESUMABLE_WAITING_PHASES)
        )
    )


def _preserve_existing_plan(
    *,
    normalized_current_input: str,
    has_resumable_plan: bool,
    decision_feasibility_state: dict[str, Any],
) -> bool:
    return bool(
        has_resumable_plan
        and (
            normalized_current_input in _RESUME_LIKE_INPUTS
            or (
                decision_feasibility_state.get("awaiting_user_choice")
                and normalized_current_input in _FOLLOWUP_CONTROL_INPUTS
            )
        )
    )


def _preserve_decision_state(
    *,
    normalized_current_input: str,
    has_resumable_plan: bool,
    has_pending_continuation_reply: bool,
) -> bool:
    return bool(
        normalized_current_input in _FOLLOWUP_CONTROL_INPUTS
        and (has_resumable_plan or has_pending_continuation_reply)
    )


def _base_turn_reset_state(
    *,
    state_inline: dict[str, Any],
    user_input: str,
    preservation: _TurnResetPreservation,
) -> dict[str, Any]:
    updated = dict(state_inline)
    pending_llm_clarify_context = state_inline.get("pending_llm_clarify_context")
    status = str(state_inline.get("status", "") or "").strip().lower()
    unresolved_clarify_items = state_inline.get("unresolved_clarify_items")
    preserve_llm_clarify_context = bool(
        isinstance(pending_llm_clarify_context, dict)
        and status == "waiting_user"
        and not list(unresolved_clarify_items or [])
    )
    updated["status"] = "waiting_user" if preserve_llm_clarify_context else "active"
    updated["phase"] = None
    if (
        preservation.preserve_pending_confirmation
        and preservation.parsed_confirmation_reply in {"affirm", "deny"}
    ):
        updated["last_user_input"] = str(
            state_inline.get("last_user_input", "") or ""
        ).strip()
    else:
        updated["last_user_input"] = str(user_input or "").strip()
    updated["retries_for_step"] = {}
    updated["pending_jobs"] = []
    updated["unresolved_clarify_items"] = []
    updated["pending_clarify_items"] = []
    updated["clarify_responses"] = {}
    updated["clarify_resume_cursor"] = None
    updated["constraints"] = []
    updated["last_result"] = None
    updated["last_command_id"] = None
    updated["step_outputs"] = []
    updated["recent_artifacts"] = []
    updated["reflection_backlog"] = []
    updated["gateway_system_context"] = ""
    updated["resume_task_id_hint"] = None
    updated["resume_cron_job_id_hint"] = None
    updated["memory_candidates"] = []
    updated["active_workflow_name"] = None
    updated["active_workflow_kind"] = None
    updated["pending_llm_clarify_context"] = (
        dict(pending_llm_clarify_context) if preserve_llm_clarify_context else None
    )
    pending_turn_context, pending_turn_context_stale_turns = (
        preserve_pending_turn_context_on_new_input(state_inline=state_inline)
    )
    updated["pending_turn_context"] = pending_turn_context
    updated["pending_turn_context_stale_turns"] = pending_turn_context_stale_turns
    return updated


def _apply_pending_confirmation_reset(
    *,
    updated: dict[str, Any],
    state_inline: dict[str, Any],
    preservation: _TurnResetPreservation,
) -> None:
    if preservation.preserve_pending_confirmation:
        updated["pending_confirmation_command"] = (
            preservation.pending_confirmation_command
        )
        updated["pending_confirmation_sub_intents"] = list(
            state_inline.get("pending_confirmation_sub_intents", []) or []
        )
        updated["pending_confirmation_sub_intent_refs"] = list(
            state_inline.get("pending_confirmation_sub_intent_refs", []) or []
        )
        updated["pending_confirmation_rationale"] = str(
            state_inline.get("pending_confirmation_rationale", "") or ""
        )
        pending_confirmation_success_criteria = state_inline.get(
            "pending_confirmation_success_criteria",
            {},
        )
        updated["pending_confirmation_success_criteria"] = (
            dict(pending_confirmation_success_criteria)
            if isinstance(pending_confirmation_success_criteria, dict)
            else {}
        )
        pending_confirmation_feasibility_state = state_inline.get(
            "pending_confirmation_feasibility_state",
            {},
        )
        updated["pending_confirmation_feasibility_state"] = (
            dict(pending_confirmation_feasibility_state)
            if isinstance(pending_confirmation_feasibility_state, dict)
            else {}
        )
        updated["pending_confirmation_feasibility_report"] = state_inline.get(
            "pending_confirmation_feasibility_report"
        )
        return
    updated["pending_confirmation_command"] = None
    updated["pending_confirmation_sub_intents"] = []
    updated["pending_confirmation_sub_intent_refs"] = []
    updated["pending_confirmation_rationale"] = ""
    updated["pending_confirmation_success_criteria"] = {}
    updated["pending_confirmation_feasibility_state"] = {}
    updated["pending_confirmation_feasibility_report"] = None


def _apply_decision_state_reset(
    *,
    updated: dict[str, Any],
    state_inline: dict[str, Any],
    preservation: _TurnResetPreservation,
) -> None:
    if preservation.preserve_decision_state:
        updated["decision_sub_intents"] = list(
            state_inline.get("decision_sub_intents", []) or []
        )
        updated["decision_sub_intent_refs"] = list(
            state_inline.get("decision_sub_intent_refs", []) or []
        )
        updated["decision_rationale"] = str(
            state_inline.get("decision_rationale", "") or ""
        )
        decision_success_criteria = state_inline.get(
            "decision_success_criteria",
            {},
        )
        updated["decision_success_criteria"] = (
            dict(decision_success_criteria)
            if isinstance(decision_success_criteria, dict)
            else {}
        )
        updated["decision_feasibility_state"] = dict(
            preservation.decision_feasibility_state
        )
        updated["decision_feasibility_report"] = state_inline.get(
            "decision_feasibility_report"
        )
        updated["adaptive_satisfied_intent_ids"] = list(
            state_inline.get("adaptive_satisfied_intent_ids", []) or []
        )
        updated["last_adaptive_revision_checkpoint"] = state_inline.get(
            "last_adaptive_revision_checkpoint"
        )
        updated["last_progress_checkpoint"] = state_inline.get(
            "last_progress_checkpoint"
        )
        updated["last_step_risk_assessment"] = state_inline.get(
            "last_step_risk_assessment"
        )
        updated["intent_execution_states"] = list(
            state_inline.get("intent_execution_states", []) or []
        )
        return
    updated["decision_sub_intents"] = []
    updated["decision_sub_intent_refs"] = []
    updated["decision_rationale"] = ""
    updated["decision_success_criteria"] = {}
    updated["decision_feasibility_state"] = {}
    updated["decision_feasibility_report"] = None
    updated["adaptive_satisfied_intent_ids"] = []
    updated["last_adaptive_revision_checkpoint"] = None
    updated["last_progress_checkpoint"] = None
    updated["last_step_risk_assessment"] = None
    updated["intent_execution_states"] = []


def _apply_continuation_guard_reset(
    *,
    updated: dict[str, Any],
    state_inline: dict[str, Any],
    preservation: _TurnResetPreservation,
) -> None:
    if preservation.preserve_continuation_guard:
        updated["continuation_guard_command_signature"] = (
            str(
                state_inline.get("continuation_guard_command_signature", "") or ""
            ).strip()
            or None
        )
        updated["continuation_guard_reason"] = str(
            state_inline.get("continuation_guard_reason", "") or ""
        )
    else:
        updated["continuation_guard_command_signature"] = None
        updated["continuation_guard_reason"] = ""
    if preservation.preserve_continuation_reply:
        updated["awaiting_continuation_reply"] = True
        updated["constraints"] = list(preservation.continuation_constraints)
        return
    updated["awaiting_continuation_reply"] = False


def _apply_plan_and_goal_reset(
    *,
    updated: dict[str, Any],
    state_inline: dict[str, Any],
    user_input: str,
    preservation: _TurnResetPreservation,
    mission_preview: _MissionResetPreview,
) -> None:
    parsed_state = mission_preview.parsed_state
    user_input_text = str(user_input).strip()
    if preservation.preserve_existing_plan:
        updated["plan"] = state_inline.get("plan")
        updated["cursor"] = state_inline.get("cursor", 0)
        if (
            mission_preview.mission_runtime_active
            and parsed_state is not None
            and parsed_state.mission is not None
            and mission_is_active(parsed_state)
            and mission_preview.route_action in {"continue", "finish", "ordinary"}
        ):
            updated["goal"] = parsed_state.mission.objective
        else:
            updated["goal"] = preservation.previous_goal
        return

    updated["plan"] = None
    updated["cursor"] = 0
    if mission_preview.mission_runtime_active and parsed_state is not None:
        if parsed_state.mission is not None:
            if mission_preview.route_action in {"continue", "finish", "ordinary"}:
                if mission_is_active(parsed_state):
                    updated["goal"] = parsed_state.mission.objective
                else:
                    updated["goal"] = (
                        preservation.previous_goal
                        if preservation.preserve_followup_goal
                        else user_input_text
                    )
            elif (
                mission_preview.route_action == "revise"
                and mission_preview.route_objective
            ):
                updated["goal"] = mission_preview.route_objective
            elif (
                mission_preview.route_action == "fork"
                and mission_preview.route_fork_input
            ):
                updated["goal"] = mission_preview.route_fork_input
            elif mission_preview.route_action in {"pause", "cancel"}:
                updated["goal"] = parsed_state.mission.objective
            else:
                updated["goal"] = (
                    preservation.previous_goal
                    if preservation.preserve_followup_goal
                    else user_input_text
                )
            return
    updated["goal"] = (
        preservation.previous_goal
        if preservation.preserve_followup_goal
        else user_input_text
    )


def _apply_open_questions_and_budget_reset(
    *,
    updated: dict[str, Any],
    runner: BrainRunner,
    user_input: str,
    preserve_existing_plan: bool,
) -> None:
    del user_input, preserve_existing_plan
    updated["open_questions"] = []
    ordinary_llm_calls_max = llm_calls_max_from_runner(runner)
    updated["llm_calls_used"] = 0
    updated["llm_calls_max"] = ordinary_llm_calls_max
    updated["budgets_remaining"] = {
        "ticks": runner.profile.budgets.max_ticks_per_user_turn,
        "tool_calls": runner.profile.budgets.max_tool_calls,
        "a2a_calls": runner.profile.budgets.max_a2a_calls,
        "tokens": runner.profile.budgets.max_total_llm_tokens,
        "time_ms": runner.profile.budgets.max_elapsed_ms,
    }


def _apply_task_backed_reset(
    *,
    updated: dict[str, Any],
    state_inline: dict[str, Any],
    preservation: _TurnResetPreservation,
) -> None:
    preserve_task_backed = bool(
        preservation.preserve_existing_plan or preservation.preserve_followup_goal
    )
    if preserve_task_backed:
        updated["task_backed_task_id"] = state_inline.get("task_backed_task_id")
        updated["task_backed_checkpoint_id"] = state_inline.get(
            "task_backed_checkpoint_id"
        )
        resume_state = state_inline.get(STATE_KEY_TASK_BACKED_RESUME)
        updated[STATE_KEY_TASK_BACKED_RESUME] = (
            dict(resume_state) if isinstance(resume_state, dict) else {}
        )
        return
    updated["task_backed_task_id"] = None
    updated["task_backed_checkpoint_id"] = None
    updated[STATE_KEY_TASK_BACKED_RESUME] = {}


def _apply_mission_reset_preview(
    *,
    updated: dict[str, Any],
    runner: BrainRunner,
    mission_preview: _MissionResetPreview,
) -> None:
    parsed_state = mission_preview.parsed_state
    if not (
        mission_preview.mission_runtime_active
        and parsed_state is not None
        and parsed_state.mission is not None
    ):
        return
    policy_name = reset_policy_for(route_action=mission_preview.route_action).name
    if mission_preview.route_action == "revise" and mission_preview.route_objective:
        update_mission_objective(
            mission=parsed_state.mission,
            objective=mission_preview.route_objective,
        )
    elif mission_preview.route_action == "pause":
        set_mission_status(
            mission=parsed_state.mission,
            status="paused",
            reason="mission paused by user",
            route_action=mission_preview.route_action,
        )
    elif mission_preview.route_action == "cancel":
        set_mission_status(
            mission=parsed_state.mission,
            status="cancelled",
            reason="mission cancelled by user",
            route_action=mission_preview.route_action,
        )
    elif mission_preview.route_action == "fork":
        set_mission_status(
            mission=parsed_state.mission,
            status="paused",
            reason="mission paused by forked ordinary turn",
            route_action=mission_preview.route_action,
        )
    else:
        parsed_state.mission.latest_route_action = mission_preview.route_action
        parsed_state.mission.latest_reset_policy = policy_name
    if mission_preview.route_action in {"continue", "finish", "revise"}:
        total = parsed_state.mission.budget.total_remaining
        per_turn = parsed_state.mission.budget.per_turn_max
        preview_budget = {
            "ticks": min(int(total.ticks), int(per_turn.ticks)),
            "tool_calls": min(int(total.tool_calls), int(per_turn.tool_calls)),
            "a2a_calls": min(int(total.a2a_calls), int(per_turn.a2a_calls)),
            "tokens": min(int(total.tokens), int(per_turn.tokens)),
            "time_ms": min(int(total.time_ms), int(per_turn.time_ms)),
        }
        updated["llm_calls_used"] = 0
        updated["llm_calls_max"] = min(
            llm_calls_max_from_runner(runner),
            int(parsed_state.mission.budget.llm_calls_per_turn_max or 0)
            or llm_calls_max_from_runner(runner),
        )
        updated["budgets_remaining"] = preview_budget
    update_mission_task(runner=runner, mission=parsed_state.mission)
    updated["mission"] = parsed_state.mission.model_dump(mode="json")


def _emit_confirmation_reset_preserved(
    *,
    runner: BrainRunner,
    session_id: str,
    preservation: _TurnResetPreservation,
) -> None:
    """BBPC: emit `brain.bridge.confirmation_reset_preserved` from the"""
    session_api = getattr(runner, "session_api", None)
    if session_api is None or not hasattr(session_api, "append_event"):
        return
    pending = getattr(preservation, "pending_confirmation_command", None)
    command_kind = ""
    if pending is not None:
        kind_attr = getattr(pending, "kind", None)
        if isinstance(kind_attr, str):
            command_kind = kind_attr.strip()
        elif isinstance(pending, dict):
            command_kind = str(pending.get("kind", "") or "").strip()
    payload = {
        "reply": preservation.parsed_confirmation_reply,
        "command_kind": command_kind,
    }
    try:
        session_api.append_event(
            session_id,
            BRAIN_BRIDGE_CONFIRMATION_RESET_PRESERVED_EVENT,
            payload,
        )
    except Exception:  # noqa: BLE001
        return


def _reset_state_for_new_input(
    self, *, runner: BrainRunner, session_id: str, user_input: str
) -> None:
    if not str(user_input or "").strip():
        return
    state_inline = self._latest_working_state_inline(
        runner=runner,
        session_id=session_id,
    )
    if state_inline is None:
        return
    status = str(state_inline.get("status", "")).strip().lower()
    if status == "job_pending":
        return
    mission_preview = self._mission_reset_preview(
        runner=runner,
        state_inline=state_inline,
        user_input=user_input,
    )
    # BBPC: when there is a pending confirmation, ask the policy parser
    parsed_confirmation_reply = ""
    if state_inline.get("pending_confirmation_command") is not None:
        try:
            parsed_confirmation_reply = _parse_confirmation_response(
                runner, str(user_input)
            )
        except Exception:  # noqa: BLE001
            parsed_confirmation_reply = ""
    preservation = self._turn_reset_preservation(
        state_inline=state_inline,
        user_input=user_input,
        parsed_confirmation_reply=parsed_confirmation_reply,
    )
    updated = self._base_turn_reset_state(
        state_inline=state_inline,
        user_input=user_input,
        preservation=preservation,
    )
    self._apply_pending_confirmation_reset(
        updated=updated,
        state_inline=state_inline,
        preservation=preservation,
    )
    # BBPC: emit telemetry when the new parser-driven path is what
    if (
        preservation.preserve_pending_confirmation
        and preservation.parsed_confirmation_reply in {"affirm", "deny"}
    ):
        _emit_confirmation_reset_preserved(
            runner=runner,
            session_id=session_id,
            preservation=preservation,
        )
    self._apply_decision_state_reset(
        updated=updated,
        state_inline=state_inline,
        preservation=preservation,
    )
    self._apply_continuation_guard_reset(
        updated=updated,
        state_inline=state_inline,
        preservation=preservation,
    )
    self._apply_plan_and_goal_reset(
        updated=updated,
        state_inline=state_inline,
        user_input=user_input,
        preservation=preservation,
        mission_preview=mission_preview,
    )
    self._apply_open_questions_and_budget_reset(
        updated=updated,
        runner=runner,
        user_input=user_input,
        preserve_existing_plan=preservation.preserve_existing_plan,
    )
    self._apply_task_backed_reset(
        updated=updated,
        state_inline=state_inline,
        preservation=preservation,
    )
    self._apply_mission_reset_preview(
        updated=updated,
        runner=runner,
        mission_preview=mission_preview,
    )
    self._write_working_state_inline(
        runner=runner,
        session_id=session_id,
        state_inline=updated,
    )


__all__ = [
    "BRAIN_BRIDGE_CONFIRMATION_RESET_PRESERVED_EVENT",
    "_MissionResetPreview",
    "_TurnResetPreservation",
    "_apply_continuation_guard_reset",
    "_apply_decision_state_reset",
    "_apply_mission_reset_preview",
    "_apply_open_questions_and_budget_reset",
    "_apply_pending_confirmation_reset",
    "_apply_plan_and_goal_reset",
    "_apply_task_backed_reset",
    "_base_turn_reset_state",
    "_emit_confirmation_reset_preserved",
    "_latest_working_state_inline",
    "_mission_reset_preview",
    "_reset_state_for_new_input",
    "_turn_reset_preservation",
    "_write_working_state_inline",
]
