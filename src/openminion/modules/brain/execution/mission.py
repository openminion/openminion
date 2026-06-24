from dataclasses import dataclass
from typing import Any

from ..constants import (
    BRAIN_MISSION_ROUTE_CANCEL,
    BRAIN_MISSION_ROUTE_CONTINUE,
    BRAIN_MISSION_ROUTE_FINISH,
    BRAIN_MISSION_ROUTE_FORK,
    BRAIN_MISSION_ROUTE_ORDINARY,
    BRAIN_MISSION_ROUTE_PAUSE,
    BRAIN_MISSION_ROUTE_REVISE,
    BRAIN_MISSION_ROUTE_START,
    BRAIN_MISSION_STATUS_ACTIVE,
    BRAIN_MISSION_STATUS_AWAITING_ASYNC,
    BRAIN_MISSION_STATUS_CANCELLED,
    BRAIN_MISSION_STATUS_COMPLETED,
    BRAIN_MISSION_STATUS_HALTED,
    BRAIN_RESET_POLICY_CONFIRMATION,
    BRAIN_RESET_POLICY_MISSION_CONTINUE,
    BRAIN_RESET_POLICY_MISSION_FINISH,
    BRAIN_RESET_POLICY_MISSION_FORK,
    BRAIN_RESET_POLICY_MISSION_REVISE,
    BRAIN_RESET_POLICY_MISSION_START,
    BRAIN_RESET_POLICY_ORDINARY,
)
from ..schemas import (
    BudgetCounters,
    MissionBudgetEnvelope,
    MissionState,
    WorkingState,
    iso_now,
    new_uuid,
)

_MISSION_START_PREFIXES = (
    "mission:",
    "start mission:",
    "start a mission:",
    "start mission,",
    "start a mission,",
)
_MISSION_REVISE_PREFIXES = (
    "revise mission:",
    "update mission:",
    "change mission:",
)
_MISSION_FORK_PREFIXES = ("fork:", "new turn:")
_MISSION_CONTINUE_INPUTS = {
    "continue",
    "continue mission",
    "continue the mission",
    "continue active mission",
    "continue the active mission",
}
_MISSION_FINISH_INPUTS = {
    "finish mission",
    "finish the mission",
    "complete mission",
    "complete the mission",
}
_MISSION_PAUSE_INPUTS = {
    "pause mission",
    "pause the mission",
    "pause active mission",
    "pause the active mission",
}
_MISSION_CANCEL_INPUTS = {
    "cancel mission",
    "cancel the mission",
    "stop mission",
    "stop the mission",
}

_MISSION_TERMINAL_STATUSES = {
    BRAIN_MISSION_STATUS_COMPLETED,
    BRAIN_MISSION_STATUS_CANCELLED,
    BRAIN_MISSION_STATUS_HALTED,
}


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_lower(value: Any) -> str:
    return " ".join(_normalized_text(value).lower().split())


def _mission_status(mission: MissionState | None) -> str:
    return _normalized_text(getattr(mission, "status", ""))


def _copy_budget(budget: BudgetCounters) -> BudgetCounters:
    return budget.model_copy(deep=True)


def _budget_from_profile(runner: Any) -> BudgetCounters:
    profile_budgets = getattr(getattr(runner, "profile", None), "budgets", object())
    return BudgetCounters(
        ticks=max(0, int(getattr(profile_budgets, "max_ticks_per_user_turn", 0) or 0)),
        tool_calls=max(0, int(getattr(profile_budgets, "max_tool_calls", 0) or 0)),
        a2a_calls=max(0, int(getattr(profile_budgets, "max_a2a_calls", 0) or 0)),
        tokens=max(0, int(getattr(profile_budgets, "max_total_llm_tokens", 0) or 0)),
        time_ms=max(0, int(getattr(profile_budgets, "max_elapsed_ms", 0) or 0)),
    )


def llm_calls_max_from_runner(runner: Any) -> int:
    ticks = max(
        1,
        int(
            getattr(
                getattr(getattr(runner, "profile", None), "budgets", object()),
                "max_ticks_per_user_turn",
                8,
            )
            or 8
        ),
    )
    return max(8, min(32, ticks))


def mission_is_active(state: WorkingState | None) -> bool:
    mission = getattr(state, "mission", None)
    if mission is None:
        return False
    return _mission_status(mission) in {
        BRAIN_MISSION_STATUS_ACTIVE,
        BRAIN_MISSION_STATUS_AWAITING_ASYNC,
    }


def mission_enabled(runner: Any) -> bool:
    config = getattr(getattr(runner, "options", None), "mission_config", None)
    if config is None:
        return True
    return bool(getattr(config, "enabled", True))


def _extract_prefixed_payload(*, text: str, prefixes: tuple[str, ...]) -> str:
    lowered = text.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip()
    return ""


@dataclass(frozen=True)
class MissionInputRoute:
    action: str
    objective: str = ""
    ordinary_input: str = ""


@dataclass(frozen=True)
class TurnResetPolicy:
    name: str
    overwrite_goal: bool = True
    clear_step_outputs: bool = True
    clear_adaptive_state: bool = True
    reset_failure_counters: bool = True
    reset_checkpoint_cursor: bool = True
    reset_llm_calls: bool = False
    refresh_budgets: bool = False
    clear_open_questions: bool = True


def resolve_mission_input_route(
    *,
    state: WorkingState,
    user_input: str | None,
) -> MissionInputRoute:
    text = _normalized_text(user_input)
    lowered = _normalized_lower(user_input)
    mission = getattr(state, "mission", None)
    mission_status = _mission_status(mission)
    if mission is None or mission_status in _MISSION_TERMINAL_STATUSES:
        payload = _extract_prefixed_payload(text=text, prefixes=_MISSION_START_PREFIXES)
        if payload:
            return MissionInputRoute(
                action=BRAIN_MISSION_ROUTE_START,
                objective=payload,
            )
        return MissionInputRoute(action=BRAIN_MISSION_ROUTE_ORDINARY)

    if lowered in _MISSION_CONTINUE_INPUTS:
        return MissionInputRoute(action=BRAIN_MISSION_ROUTE_CONTINUE)
    if lowered in _MISSION_FINISH_INPUTS:
        return MissionInputRoute(action=BRAIN_MISSION_ROUTE_FINISH)
    if lowered in _MISSION_PAUSE_INPUTS:
        return MissionInputRoute(action=BRAIN_MISSION_ROUTE_PAUSE)
    if lowered in _MISSION_CANCEL_INPUTS:
        return MissionInputRoute(action=BRAIN_MISSION_ROUTE_CANCEL)

    revise_payload = _extract_prefixed_payload(
        text=text, prefixes=_MISSION_REVISE_PREFIXES
    )
    if revise_payload:
        return MissionInputRoute(
            action=BRAIN_MISSION_ROUTE_REVISE,
            objective=revise_payload,
        )

    fork_payload = _extract_prefixed_payload(text=text, prefixes=_MISSION_FORK_PREFIXES)
    if fork_payload:
        return MissionInputRoute(
            action=BRAIN_MISSION_ROUTE_FORK,
            ordinary_input=fork_payload,
        )
    return MissionInputRoute(action=BRAIN_MISSION_ROUTE_ORDINARY)


def reset_policy_for(
    *,
    route_action: str,
    is_confirmation_turn: bool = False,
) -> TurnResetPolicy:
    if is_confirmation_turn:
        return TurnResetPolicy(
            name=BRAIN_RESET_POLICY_CONFIRMATION, overwrite_goal=False
        )
    if route_action == BRAIN_MISSION_ROUTE_START:
        return TurnResetPolicy(
            name=BRAIN_RESET_POLICY_MISSION_START,
            reset_llm_calls=True,
            refresh_budgets=True,
        )
    if route_action == BRAIN_MISSION_ROUTE_REVISE:
        return TurnResetPolicy(
            name=BRAIN_RESET_POLICY_MISSION_REVISE,
            reset_llm_calls=True,
            refresh_budgets=True,
        )
    if route_action == BRAIN_MISSION_ROUTE_CONTINUE:
        return TurnResetPolicy(
            name=BRAIN_RESET_POLICY_MISSION_CONTINUE,
            overwrite_goal=False,
            reset_llm_calls=True,
            refresh_budgets=True,
        )
    if route_action == BRAIN_MISSION_ROUTE_FINISH:
        return TurnResetPolicy(
            name=BRAIN_RESET_POLICY_MISSION_FINISH,
            overwrite_goal=False,
            reset_llm_calls=True,
            refresh_budgets=True,
        )
    if route_action == BRAIN_MISSION_ROUTE_FORK:
        return TurnResetPolicy(
            name=BRAIN_RESET_POLICY_MISSION_FORK,
            reset_llm_calls=True,
            refresh_budgets=True,
        )
    return TurnResetPolicy(name=BRAIN_RESET_POLICY_ORDINARY)


def build_mission_budget(
    *,
    runner: Any,
) -> MissionBudgetEnvelope:
    per_turn_budget = _budget_from_profile(runner)
    max_turns = max(
        1,
        int(
            getattr(
                getattr(getattr(runner, "options", None), "mission_config", object()),
                "max_turns_per_mission",
                4,
            )
            or 4
        ),
    )
    per_turn_llm_calls = llm_calls_max_from_runner(runner)
    return MissionBudgetEnvelope(
        total_remaining=BudgetCounters(
            ticks=per_turn_budget.ticks * max_turns,
            tool_calls=per_turn_budget.tool_calls * max_turns,
            a2a_calls=per_turn_budget.a2a_calls * max_turns,
            tokens=per_turn_budget.tokens * max_turns,
            time_ms=per_turn_budget.time_ms * max_turns,
        ),
        per_turn_max=_copy_budget(per_turn_budget),
        remaining_llm_calls_total=per_turn_llm_calls * max_turns,
        llm_calls_per_turn_max=per_turn_llm_calls,
    )


def ensure_mission_task(
    *,
    runner: Any,
    state: WorkingState,
    mission: MissionState,
) -> None:
    if mission.task_id:
        return
    manager = getattr(runner, "task_manager", None)
    if manager is None:
        return
    try:
        record = manager.create_task(
            session_id=state.session_id,
            mode_name="mission",
            goal=mission.objective,
            agent_id=state.agent_id,
            metadata={
                "kind": "mission",
                "mission_id": mission.mission_id,
                "mission_status": mission.status,
            },
        )
    except Exception:
        return
    mission.task_id = _normalized_text(getattr(record, "task_id", ""))


def update_mission_task(
    *,
    runner: Any,
    mission: MissionState,
    to_state: str | None = None,
) -> None:
    manager = getattr(runner, "task_manager", None)
    if manager is None or not mission.task_id:
        return
    metadata = {
        "kind": "mission",
        "mission_id": mission.mission_id,
        "mission_status": mission.status,
        "goal": mission.objective,
    }
    try:
        manager.update_task_metadata(task_id=mission.task_id, metadata=metadata)
    except Exception:
        pass
    if to_state:
        try:
            manager.transition_task(task_id=mission.task_id, to_state=to_state)
        except Exception:
            pass


def build_mission_state(
    *,
    runner: Any,
    state: WorkingState,
    objective: str,
) -> MissionState:
    mission = MissionState(
        mission_id=new_uuid(),
        objective=_normalized_text(objective),
        status=BRAIN_MISSION_STATUS_ACTIVE,
        budget=build_mission_budget(runner=runner),
        last_progress_at=iso_now(),
        latest_route_action=BRAIN_MISSION_ROUTE_START,
    )
    ensure_mission_task(runner=runner, state=state, mission=mission)
    return mission


def update_mission_objective(
    *,
    mission: MissionState,
    objective: str,
) -> None:
    mission.objective = _normalized_text(objective)
    mission.latest_route_action = BRAIN_MISSION_ROUTE_REVISE
    mission.latest_reason = "mission objective revised"
    mission.last_progress_at = iso_now()
    mission.status = BRAIN_MISSION_STATUS_ACTIVE


def allocate_mission_turn_budget(
    *,
    runner: Any,
    state: WorkingState,
) -> None:
    mission = getattr(state, "mission", None)
    if mission is None:
        return
    budget = mission.budget
    per_turn = budget.per_turn_max
    total = budget.total_remaining
    allocated = BudgetCounters(
        ticks=min(total.ticks, per_turn.ticks),
        tool_calls=min(total.tool_calls, per_turn.tool_calls),
        a2a_calls=min(total.a2a_calls, per_turn.a2a_calls),
        tokens=min(total.tokens, per_turn.tokens),
        time_ms=min(total.time_ms, per_turn.time_ms),
    )
    budget.turn_budget_baseline = _copy_budget(total)
    budget.turn_budget_allocated = _copy_budget(allocated)
    budget.turn_llm_calls_baseline_total = int(budget.remaining_llm_calls_total)
    budget.turns_started += 1
    state.budgets_remaining = allocated
    state.llm_calls_used = 0
    state.llm_calls_max = min(
        llm_calls_max_from_runner(runner),
        int(budget.llm_calls_per_turn_max or 0) or llm_calls_max_from_runner(runner),
    )
    mission.last_progress_at = iso_now()


def sync_mission_budget_progress(state: WorkingState) -> None:
    mission = getattr(state, "mission", None)
    if mission is None:
        return
    budget = mission.budget
    baseline = budget.turn_budget_baseline
    allocated = budget.turn_budget_allocated
    if baseline is None or allocated is None:
        return
    budget.total_remaining = BudgetCounters(
        ticks=max(
            0, baseline.ticks - max(0, allocated.ticks - state.budgets_remaining.ticks)
        ),
        tool_calls=max(
            0,
            baseline.tool_calls
            - max(0, allocated.tool_calls - state.budgets_remaining.tool_calls),
        ),
        a2a_calls=max(
            0,
            baseline.a2a_calls
            - max(0, allocated.a2a_calls - state.budgets_remaining.a2a_calls),
        ),
        tokens=max(
            0,
            baseline.tokens - max(0, allocated.tokens - state.budgets_remaining.tokens),
        ),
        time_ms=max(
            0,
            baseline.time_ms
            - max(0, allocated.time_ms - state.budgets_remaining.time_ms),
        ),
    )
    if budget.turn_llm_calls_baseline_total is not None:
        budget.remaining_llm_calls_total = max(
            0,
            int(budget.turn_llm_calls_baseline_total)
            - max(0, int(state.llm_calls_used or 0)),
        )
    mission.last_progress_at = iso_now()


def apply_turn_reset_policy(
    *,
    state: WorkingState,
    policy: TurnResetPolicy,
    next_goal: str | None,
    turn_budget: BudgetCounters | None = None,
    llm_calls_max: int | None = None,
) -> None:
    if policy.overwrite_goal:
        normalized_goal = _normalized_text(next_goal)
        state.goal = normalized_goal or None
    if policy.clear_step_outputs:
        state.step_outputs = []
    if policy.clear_adaptive_state:
        state.adaptive_satisfied_intent_ids = []
        state.last_adaptive_revision_checkpoint = None
    if policy.reset_failure_counters:
        state.consecutive_step_failures = 0
        state.retries_for_step = {}
    if policy.reset_checkpoint_cursor:
        state.last_checkpoint_cursor = -1
    if policy.clear_open_questions:
        state.open_questions = []
    if policy.reset_llm_calls:
        state.llm_calls_used = 0
        if llm_calls_max is not None:
            state.llm_calls_max = max(1, int(llm_calls_max))
    if policy.refresh_budgets and turn_budget is not None:
        state.budgets_remaining = _copy_budget(turn_budget)


def set_mission_status(
    *,
    mission: MissionState,
    status: str,
    reason: str,
    route_action: str,
) -> None:
    mission.status = status
    mission.latest_reason = _normalized_text(reason)
    mission.latest_route_action = route_action
    mission.last_progress_at = iso_now()
    if status == BRAIN_MISSION_STATUS_COMPLETED:
        mission.completed_at = iso_now()


def continue_message(mission: MissionState) -> str:
    return (
        "Mission remains active. Continue, revise, pause, cancel, or fork a new turn.\n"
        f"Objective: {mission.objective}"
    )


__all__ = [
    "MissionInputRoute",
    "TurnResetPolicy",
    "allocate_mission_turn_budget",
    "apply_turn_reset_policy",
    "build_mission_state",
    "continue_message",
    "ensure_mission_task",
    "llm_calls_max_from_runner",
    "mission_enabled",
    "mission_is_active",
    "reset_policy_for",
    "resolve_mission_input_route",
    "set_mission_status",
    "sync_mission_budget_progress",
    "update_mission_objective",
    "update_mission_task",
]
