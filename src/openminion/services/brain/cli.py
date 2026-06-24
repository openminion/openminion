from pathlib import Path
from typing import Literal

from openminion.modules.brain.runtime.goal.long_running import (
    LongRunningGoalRuntime,
    render_goal_summary,
    render_goal_verification,
)
from openminion.modules.brain.schemas.goals import Goal
from openminion.modules.brain.schemas.state import BudgetCounters, WorkingState
from openminion.modules.brain.storage.goals import SQLiteGoalStore
from openminion.modules.brain.storage.missions import SQLiteMissionStateStore


GoalCliTone = Literal["info", "success", "error"]


class _CliLogger:
    def emit(
        self,
        event: str,
        payload: dict[str, object],
        *,
        trace_id: str,
        status: str,
    ) -> None:
        del event, payload, trace_id, status


def build_goal_cli_runtime(db_path: Path) -> LongRunningGoalRuntime:
    return LongRunningGoalRuntime(
        goal_store=SQLiteGoalStore(db_path),
        mission_store=SQLiteMissionStateStore(db_path),
    )


def _state(session_id: str) -> WorkingState:
    return WorkingState(
        session_id=session_id or "cli-goal-session",
        agent_id="cli",
        budgets_remaining=BudgetCounters(
            ticks=1,
            tool_calls=1,
            a2a_calls=0,
            tokens=1,
            time_ms=1,
        ),
        trace_id="goal-cli",
    )


def _session_goal_or_error(
    runtime: LongRunningGoalRuntime,
    *,
    goal_id: str,
    session_id: str,
) -> tuple[Goal | None, str]:
    goal_store = runtime.goal_store
    goal = goal_store.get(goal_id)
    if goal is None:
        return (None, f"Unknown goal: {goal_id}")
    if not goal_store.is_bound_to_session(goal.goal_id, session_id):
        return (None, f"Goal is not active for this session: {goal_id}")
    return (goal, "")


def execute_goal_cli_command(
    line: str,
    *,
    session_id: str,
    db_path: Path,
) -> tuple[GoalCliTone, str]:
    stripped = (line or "").strip()
    runtime = build_goal_cli_runtime(db_path)
    goal_store = runtime.goal_store

    if stripped in {"/goal", "/goal list"}:
        goals = goal_store.list_active_for_session(session_id)
        if not goals:
            return ("info", "No active goals for this session.")
        return ("info", "\n".join(render_goal_summary(goal) for goal in goals))

    if stripped in {"/goal all", "/goals"}:
        goals = goal_store.list_active()
        if not goals:
            return ("info", "No active workspace goals.")
        return ("info", "\n".join(render_goal_summary(goal) for goal in goals))

    if stripped.startswith("/goal show "):
        goal_id = stripped.split(" ", 2)[2].strip()
        goal, error = _session_goal_or_error(
            runtime,
            goal_id=goal_id,
            session_id=session_id,
        )
        if error:
            return ("error", error)
        details = [
            render_goal_summary(goal),
            f"success_criteria={len(goal.success_criteria)}",
            f"deliverables={len(goal.deliverables)}",
            f"failure_conditions={len(goal.failure_conditions)}",
        ]
        return ("info", "\n".join(details))

    if stripped.startswith("/goal abort "):
        goal_id = stripped.split(" ", 2)[2].strip()
        goal, error = _session_goal_or_error(
            runtime,
            goal_id=goal_id,
            session_id=session_id,
        )
        if error:
            return ("error", error)
        aborted = goal_store.abort(goal.goal_id, reason="goal_cli_abort")
        return ("success", render_goal_summary(aborted))

    if stripped.startswith("/goal verify "):
        goal_id = stripped.split(" ", 2)[2].strip()
        goal, error = _session_goal_or_error(
            runtime,
            goal_id=goal_id,
            session_id=session_id,
        )
        if error:
            return ("error", error)
        result = runtime.verify_goal_for_cli(
            goal_id=goal.goal_id,
            run_id=f"goal-cli-{goal.goal_id}",
            state=_state(session_id),
            logger=_CliLogger(),
        )
        return ("info", render_goal_verification(goal_id, result))

    return ("error", "usage: /goal [list|all|show <id>|abort <id>|verify <id>]")


__all__ = [
    "GoalCliTone",
    "build_goal_cli_runtime",
    "execute_goal_cli_command",
]
