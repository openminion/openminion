from pathlib import Path
from typing import Literal

from openminion.modules.brain.runtime.goal.long_running import (
    AutonomyRunStore,
    GoalRunController,
    LongRunningGoalRuntime,
    SQLiteGoalRunStore,
    parse_replay_evaluations,
    render_goal_run_status,
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


def build_goal_run_controller(
    runtime: LongRunningGoalRuntime,
    *,
    db_path: Path,
) -> GoalRunController:
    return GoalRunController(
        goal_store=runtime.goal_store,
        run_store=SQLiteGoalRunStore(db_path),
        proof_store=AutonomyRunStore(db_path.parent / "goal-run-proofs"),
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

    controller = build_goal_run_controller(runtime, db_path=db_path)

    if stripped == "/goal status":
        return (
            "info",
            render_goal_run_status(controller.active_state(session_id=session_id)),
        )

    if stripped in {"/goal stop", "/goal clear"}:
        stopped = controller.stop_session_run(session_id=session_id)
        if stopped is None:
            return ("info", "No active goal run for this session.")
        return ("success", render_goal_run_status(stopped))

    if stripped.startswith("/goal run "):
        return _run_goal_command(
            stripped,
            runtime=runtime,
            controller=controller,
            session_id=session_id,
        )

    return (
        "error",
        "usage: /goal [list|all|show <id>|abort <id>|verify <id>|run <id>|status|stop|clear]",
    )


def _run_goal_command(
    line: str,
    *,
    runtime: LongRunningGoalRuntime,
    controller: GoalRunController,
    session_id: str,
) -> tuple[GoalCliTone, str]:
    parts = line.split()
    if len(parts) < 3:
        return ("error", "usage: /goal run <goal_id> [--replay outcome:reason,...]")
    goal_id = parts[2].strip()
    goal, error = _session_goal_or_error(
        runtime,
        goal_id=goal_id,
        session_id=session_id,
    )
    if error:
        return ("error", error)
    replay = _option_value(parts[3:], "--replay")
    if replay:
        try:
            evaluations = parse_replay_evaluations(goal.goal_id, replay)
            state = controller.run_replay(
                session_id=session_id,
                goal_id=goal.goal_id,
                evaluations=evaluations,
            )
        except (KeyError, ValueError) as exc:
            return ("error", str(exc))
        return ("success", render_goal_run_status(state))
    try:
        state = controller.start_goal_run(
            session_id=session_id,
            goal_id=goal.goal_id,
        )
    except (KeyError, ValueError) as exc:
        return ("error", str(exc))
    return ("success", render_goal_run_status(state))


def _option_value(args: list[str], name: str) -> str:
    for index, item in enumerate(args):
        if item == name and index + 1 < len(args):
            return args[index + 1].strip()
        prefix = f"{name}="
        if item.startswith(prefix):
            return item.removeprefix(prefix).strip()
    return ""


__all__ = [
    "GoalCliTone",
    "build_goal_cli_runtime",
    "build_goal_run_controller",
    "execute_goal_cli_command",
]
