from pathlib import Path
from typing import Any, Callable, Literal, cast

from openminion.modules.brain import (
    GoalContinuationDriver,
    GoalRunController,
    GoalRunOutcome,
    GoalRunState,
    GoalTurnResult,
    GoalVerificationResult,
    LongRunningGoalRuntime,
    SQLiteGoalRunStepLedger,
    SQLiteGoalRunStore,
    build_goal_context_card,
    format_goal_focus_segment,
    parse_replay_evaluations,
    render_goal_context_card,
    render_goal_run_status,
    render_goal_summary,
    render_goal_verification,
)
from openminion.modules.brain.schemas.goals import Goal
from openminion.modules.brain.schemas.state import BudgetCounters, WorkingState
from openminion.modules.brain.storage.goals import GoalStore, SQLiteGoalStore
from openminion.modules.brain.storage.missions import SQLiteMissionStateStore
from openminion.modules.task import AutonomyRunStore


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
        return _goal_list_response(goal_store, session_id=session_id)

    if stripped in {"/goal all", "/goals"}:
        return _goal_all_response(goal_store)

    if stripped.startswith("/goal show "):
        return _goal_show_response(stripped, runtime, session_id=session_id)

    if stripped.startswith("/goal abort "):
        return _goal_abort_response(stripped, runtime, session_id=session_id)

    if stripped.startswith("/goal verify "):
        return _goal_verify_response(stripped, runtime, session_id=session_id)

    controller = build_goal_run_controller(runtime, db_path=db_path)

    if stripped.startswith("/goal run "):
        return _run_goal_command(
            stripped,
            runtime=runtime,
            controller=controller,
            session_id=session_id,
        )

    run_response = _goal_run_control_response(
        stripped,
        controller=controller,
        session_id=session_id,
        db_path=db_path,
    )
    if run_response is not None:
        return run_response

    return (
        "error",
        "usage: /goal [list|all|show <id>|abort <id>|verify <id>|run <id>|status|inspect|evidence|pause|resume|stop|clear]",
    )


def _goal_list_response(
    goal_store: GoalStore,
    *,
    session_id: str,
) -> tuple[GoalCliTone, str]:
    goals = goal_store.list_active_for_session(session_id)
    if not goals:
        return ("info", "No active goals for this session.")
    return ("info", "\n".join(render_goal_summary(goal) for goal in goals))


def _goal_all_response(goal_store: GoalStore) -> tuple[GoalCliTone, str]:
    goals = goal_store.list_active()
    if not goals:
        return ("info", "No active workspace goals.")
    return ("info", "\n".join(render_goal_summary(goal) for goal in goals))


def _goal_show_response(
    line: str,
    runtime: LongRunningGoalRuntime,
    *,
    session_id: str,
) -> tuple[GoalCliTone, str]:
    goal_id = line.split(" ", 2)[2].strip()
    goal, error = _session_goal_or_error(
        runtime, goal_id=goal_id, session_id=session_id
    )
    if error or goal is None:
        return ("error", error)
    details = [
        render_goal_summary(goal),
        f"success_criteria={len(goal.success_criteria)}",
        f"deliverables={len(goal.deliverables)}",
        f"failure_conditions={len(goal.failure_conditions)}",
    ]
    return ("info", "\n".join(details))


def _goal_abort_response(
    line: str,
    runtime: LongRunningGoalRuntime,
    *,
    session_id: str,
) -> tuple[GoalCliTone, str]:
    goal_id = line.split(" ", 2)[2].strip()
    goal, error = _session_goal_or_error(
        runtime, goal_id=goal_id, session_id=session_id
    )
    if error or goal is None:
        return ("error", error)
    aborted = runtime.goal_store.abort(goal.goal_id, reason="goal_cli_abort")
    return ("success", render_goal_summary(aborted))


def _goal_verify_response(
    line: str,
    runtime: LongRunningGoalRuntime,
    *,
    session_id: str,
) -> tuple[GoalCliTone, str]:
    goal_id = line.split(" ", 2)[2].strip()
    goal, error = _session_goal_or_error(
        runtime, goal_id=goal_id, session_id=session_id
    )
    if error or goal is None:
        return ("error", error)
    result = runtime.verify_goal_for_cli(
        goal_id=goal.goal_id,
        run_id=f"goal-cli-{goal.goal_id}",
        state=_state(session_id),
        logger=_CliLogger(),
    )
    return ("info", render_goal_verification(goal_id, result))


def _goal_run_control_response(
    line: str,
    *,
    controller: GoalRunController,
    session_id: str,
    db_path: Path,
) -> tuple[GoalCliTone, str] | None:
    if line == "/goal status":
        return (
            "info",
            render_goal_run_status(controller.active_state(session_id=session_id)),
        )
    if line == "/goal inspect":
        return _goal_inspect_response(
            controller, session_id=session_id, db_path=db_path
        )
    if line == "/goal evidence":
        return _goal_evidence_response(
            controller, session_id=session_id, db_path=db_path
        )
    if line == "/goal pause":
        paused = controller.pause_session_run(session_id=session_id)
        if paused is None:
            return ("info", "No active goal run for this session.")
        return ("success", render_goal_run_status(paused))
    if line == "/goal resume":
        resumed = controller.resume_session_run(session_id=session_id)
        if resumed is None:
            return ("info", "No paused goal run for this session.")
        return ("success", render_goal_run_status(resumed))
    if line in {"/goal stop", "/goal clear"}:
        stopped = controller.stop_session_run(session_id=session_id)
        if stopped is None:
            return ("info", "No active goal run for this session.")
        return ("success", render_goal_run_status(stopped))
    return None


def _latest_goal_run_state(
    controller: GoalRunController,
    session_id: str,
) -> GoalRunState | None:
    return controller.active_state(session_id=session_id) or (
        controller.run_store.latest_for_session(session_id)
    )


def _goal_inspect_response(
    controller: GoalRunController,
    *,
    session_id: str,
    db_path: Path,
) -> tuple[GoalCliTone, str]:
    state = _latest_goal_run_state(controller, session_id)
    if state is None:
        return ("info", "No goal run for this session.")
    summary = SQLiteGoalRunStepLedger(db_path).summary_for_run(state.run_id)
    lines = [
        render_goal_run_status(state),
        f"ledger_steps={summary.step_count}",
        f"latest_next_instruction={summary.latest_next_instruction or '-'}",
    ]
    if summary.error_refs:
        lines.append("error_refs=" + ",".join(summary.error_refs))
    goal = controller.goal_store.get(state.goal_id)
    if goal is not None:
        card = build_goal_context_card(goal=goal, state=state, summary=summary)
        lines.extend(("", render_goal_context_card(card)))
    return ("info", "\n".join(lines))


def goal_statusline_label(*, session_id: str, db_path: Path) -> str:
    runtime = build_goal_cli_runtime(db_path)
    controller = build_goal_run_controller(runtime, db_path=db_path)
    return cast(
        str,
        format_goal_focus_segment(controller.active_state(session_id=session_id)),
    )


def _goal_evidence_response(
    controller: GoalRunController,
    *,
    session_id: str,
    db_path: Path,
) -> tuple[GoalCliTone, str]:
    state = _latest_goal_run_state(controller, session_id)
    if state is None:
        return ("info", "No goal run for this session.")
    steps = SQLiteGoalRunStepLedger(db_path).list_for_run(state.run_id)
    if not steps:
        return ("info", "No goal-run evidence recorded.")
    return ("info", "\n".join(_render_goal_step(step) for step in steps))


def _render_goal_step(step: Any) -> str:
    evidence = ",".join(step.tool_evidence_refs) or "-"
    return (
        f"{step.turn_index}: {step.evaluator_outcome} "
        f"{step.evaluator_reason} evidence={evidence}"
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
    if error or goal is None:
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
    live_script = _option_value(parts[3:], "--live")
    if live_script:
        try:
            driver = GoalContinuationDriver(
                controller=controller,
                goal_store=runtime.goal_store,
                ledger=SQLiteGoalRunStepLedger(controller.run_store.sqlite_path),
            )
            state = driver.run_until_stop(
                session_id=session_id,
                goal_id=goal.goal_id,
                turn_runner=_scripted_goal_turn_runner(live_script),
                verifier=_scripted_goal_verifier,
            )
        except (KeyError, RuntimeError, ValueError) as exc:
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


def _scripted_goal_turn_runner(raw: str) -> Callable[[str], GoalTurnResult]:
    entries = [part.strip() for part in str(raw or "").split(",") if part.strip()]
    if not entries:
        raise ValueError("--live requires outcome:reason entries")
    index = 0

    def run_turn(_prompt: str) -> GoalTurnResult:
        nonlocal index
        if index >= len(entries):
            return GoalTurnResult(
                proposed_outcome="blocked",
                reason="live_script_exhausted",
            )
        entry = entries[index]
        index += 1
        outcome, sep, reason = entry.partition(":")
        if not sep:
            raise ValueError("--live entries must use outcome:reason")
        return GoalTurnResult(
            proposed_outcome=cast(GoalRunOutcome, outcome.strip()),
            reason=reason.strip() or outcome.strip(),
            evidence_refs=(f"live-script:{index}",),
            next_instruction="continue bounded goal work"
            if outcome.strip() == "continue"
            else "",
        )

    return run_turn


def _scripted_goal_verifier(
    _goal: Goal,
    _state: Any,
    result: GoalTurnResult,
) -> GoalVerificationResult | None:
    if result.proposed_outcome != "satisfied":
        return None
    return GoalVerificationResult(
        status="passed",
        unmet_criteria=(),
        missing_deliverables=(),
        triggered_failures=(),
        verifier_results=(),
    )


__all__ = [
    "GoalCliTone",
    "build_goal_cli_runtime",
    "build_goal_run_controller",
    "execute_goal_cli_command",
    "goal_statusline_label",
]
