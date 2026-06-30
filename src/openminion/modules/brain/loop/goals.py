"""Goal access helpers for the adaptive loop."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.brain.constants import STATE_KEY_MODULE_STATE
from openminion.modules.brain.schemas.goals import Goal
from openminion.modules.brain.storage.goals import GoalStore


GoalIterationOutcome = Literal[
    "advanced",
    "evidence_only",
    "blocked",
    "needs_user",
    "satisfied",
]


class GoalIterationReport(BaseModel):
    """Structured per-iteration progress fact relative to a durable goal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal_id: str = Field(min_length=1)
    outcome: GoalIterationOutcome
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()


def resolve_active_goal(
    state: Any,
    *,
    goal_store: GoalStore | None = None,
    goal_runtime: Any | None = None,
) -> Goal | None:
    """Resolve the current session's canonical goal without guessing globally."""

    goal_id = _state_goal_id(state)
    if goal_id and goal_store is not None:
        goal = goal_store.get(goal_id)
        if goal is not None and _goal_matches_session(goal_store, goal_id, state):
            return goal
        return None

    session_id = _state_session_id(state)
    if session_id and goal_runtime is not None:
        list_for_session = getattr(goal_runtime, "list_active_goals_for_session", None)
        if callable(list_for_session):
            goals = list_for_session(session_id)
            if goals:
                first = goals[0]
                if isinstance(first, Goal):
                    return first
    if session_id and goal_store is not None:
        goals = goal_store.list_active_for_session(session_id)
        if goals:
            return goals[0]
    return None


def build_goal_iteration_report(
    *,
    goal: Goal,
    outcome: GoalIterationOutcome,
    reason: str = "",
    evidence_refs: tuple[str, ...] | list[str] = (),
) -> GoalIterationReport:
    """Build the loop's structured progress fact for one iteration."""

    return GoalIterationReport(
        goal_id=goal.goal_id,
        outcome=outcome,
        reason=str(reason or "").strip(),
        evidence_refs=tuple(
            str(item).strip() for item in evidence_refs if str(item).strip()
        ),
    )


def report_goal_iteration(
    state: Any,
    *,
    outcome: GoalIterationOutcome,
    reason: str = "",
    evidence_refs: tuple[str, ...] | list[str] = (),
    goal_store: GoalStore | None = None,
    goal_runtime: Any | None = None,
) -> GoalIterationReport | None:
    """Build a goal-relative report only when the current session has a goal."""

    goal = resolve_active_goal(
        state,
        goal_store=goal_store,
        goal_runtime=goal_runtime,
    )
    if goal is None:
        return None
    return build_goal_iteration_report(
        goal=goal,
        outcome=outcome,
        reason=reason,
        evidence_refs=evidence_refs,
    )


def _state_goal_id(state: Any) -> str:
    for attr_name in ("active_goal_id", "goal_id"):
        value = getattr(state, attr_name, None)
        text = str(value or "").strip()
        if text:
            return text
    return _module_state_goal_id(state)


def _module_state_goal_id(state: Any) -> str:
    module_state = getattr(state, STATE_KEY_MODULE_STATE, None)
    if not isinstance(module_state, dict):
        return ""
    bucket = module_state.get("goal") or module_state.get("goals")
    if not isinstance(bucket, dict):
        return ""
    return str(bucket.get("active_goal_id") or bucket.get("goal_id") or "").strip()


def _state_session_id(state: Any) -> str:
    return str(getattr(state, "session_id", "") or "").strip()


def _goal_matches_session(goal_store: GoalStore, goal_id: str, state: Any) -> bool:
    session_id = _state_session_id(state)
    if not session_id:
        return False
    return goal_store.is_bound_to_session(goal_id, session_id)


__all__ = [
    "GoalIterationOutcome",
    "GoalIterationReport",
    "build_goal_iteration_report",
    "report_goal_iteration",
    "resolve_active_goal",
]
