from __future__ import annotations

from openminion.modules.brain.schemas import (
    ActionResult,
    Deliverable,
    Goal,
    Milestone,
    SuccessCriterion,
    ToolCommand,
    WorkingState,
)
from openminion.modules.brain.storage.goals import SQLiteGoalStore
from openminion.modules.brain.storage.missions import SQLiteMissionStateStore
from openminion.modules.brain.runtime.goal.verification import (
    GoalVerificationInput,
    verify_goal_completion,
)
from openminion.modules.brain.schemas import (
    BudgetCounters,
    MissionBudgetEnvelope,
    MissionState,
)


class _FakeLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(
        self,
        event: str,
        payload: dict[str, object],
        *,
        trace_id: str,
        status: str,
    ) -> None:
        self.events.append(
            (
                event,
                {
                    "trace_id": trace_id,
                    "status": status,
                    **payload,
                },
            )
        )


def _goal(goal_id: str, **overrides) -> Goal:
    return Goal(
        goal_id=goal_id,
        description="goal",
        success_criteria=[
            SuccessCriterion(
                criterion_id="criterion-1",
                description="criterion",
                structural_check="success_criteria.ok=true",
            )
        ],
        deliverables=[
            Deliverable(
                deliverable_id="deliverable-1",
                description="deliverable",
                verification_hint="artifact_presence",
            )
        ],
        **overrides,
    )


def _budget() -> MissionBudgetEnvelope:
    counters = BudgetCounters(
        ticks=10,
        tool_calls=10,
        a2a_calls=0,
        tokens=1000,
        time_ms=60000,
    )
    return MissionBudgetEnvelope(
        total_remaining=counters,
        per_turn_max=counters,
        remaining_llm_calls_total=10,
        llm_calls_per_turn_max=2,
    )


def _mission(mission_id: str) -> MissionState:
    return MissionState(
        mission_id=mission_id,
        objective="mission",
        budget=_budget(),
    )


def _verification_input(*, with_artifact: bool = False) -> GoalVerificationInput:
    return GoalVerificationInput(
        command=ToolCommand(
            title="verify",
            tool_name="noop",
            success_criteria={"ok": True},
        ),
        action_result=ActionResult(
            command_id="cmd-1",
            status="success",
            outputs={"ok": True},
            artifact_refs=(
                [{"ref": "artifact://x", "label": "x", "meta": {}}]
                if with_artifact
                else []
            ),
        ),
    )


def _working_state() -> WorkingState:
    return WorkingState(
        session_id="session-1",
        agent_id="agent-1",
        budgets_remaining={
            "ticks": 1,
            "tool_calls": 1,
            "a2a_calls": 0,
            "tokens": 1,
            "time_ms": 1,
        },
        trace_id="trace-1",
    )


def test_goal_store_pause_resume_abort_round_trip(tmp_path) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")
    store.create(_goal("goal-lifecycle"))

    paused = store.pause("goal-lifecycle", reason="operator pause")
    resumed = store.resume("goal-lifecycle", reason="operator resume")
    aborted = store.abort("goal-lifecycle", reason="operator cancelled")

    assert paused.status == "paused"
    assert resumed.status == "active"
    assert aborted.status == "cancelled"
    assert aborted.failure_conditions[-1].kind == "operator_cancelled"


def test_mission_state_store_pause_resume_abort_round_trip(tmp_path) -> None:
    store = SQLiteMissionStateStore(tmp_path / "missions.db")
    store.create(_mission("mission-lifecycle"))

    paused = store.pause("mission-lifecycle", reason="operator pause")
    resumed = store.resume("mission-lifecycle", reason="operator resume")
    aborted = store.abort("mission-lifecycle", reason="operator cancelled")

    assert paused.status == "paused"
    assert resumed.status == "active"
    assert aborted.status == "cancelled"


def test_goal_with_milestones_and_wall_clock_budget_constructs() -> None:
    goal = _goal(
        "goal-budget",
        wall_clock_budget_seconds=60,
        milestone_checkpoints=[
            Milestone(
                milestone_id="m-1",
                description="checkpoint",
                structural_check="success_criteria.ok=true",
            )
        ],
    )

    assert goal.wall_clock_budget_seconds == 60
    assert goal.milestone_checkpoints[0].milestone_id == "m-1"


def test_verify_goal_completion_returns_passed_for_complete_evidence(tmp_path) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")
    store.create(_goal("goal-verify"))

    result = verify_goal_completion(
        "goal-verify",
        goals=store,
        run_id="run-1",
        state=_working_state(),
        logger=_FakeLogger(),
        criterion_inputs={"criterion-1": _verification_input()},
        deliverable_inputs={"deliverable-1": _verification_input(with_artifact=True)},
    )

    assert result.status == "passed"
    assert result.unmet_criteria == ()
    assert result.missing_deliverables == ()
    assert result.triggered_failures == ()


def test_verify_goal_completion_returns_incomplete_when_inputs_missing(
    tmp_path,
) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")
    store.create(_goal("goal-incomplete"))

    result = verify_goal_completion(
        "goal-incomplete",
        goals=store,
        run_id="run-2",
        state=_working_state(),
        logger=_FakeLogger(),
        criterion_inputs={},
        deliverable_inputs={},
    )

    assert result.status == "incomplete"
    assert result.unmet_criteria == ("criterion-1",)
    assert result.missing_deliverables == ("deliverable-1",)


def test_verify_goal_completion_returns_failed_when_wall_clock_exceeded(
    tmp_path,
) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")
    store.create(_goal("goal-timeout", wall_clock_budget_seconds=5))

    result = verify_goal_completion(
        "goal-timeout",
        goals=store,
        run_id="run-3",
        state=_working_state(),
        logger=_FakeLogger(),
        criterion_inputs={"criterion-1": _verification_input()},
        deliverable_inputs={"deliverable-1": _verification_input(with_artifact=True)},
        elapsed_wall_clock_seconds=10,
    )

    assert result.status == "failed"
    assert len(result.triggered_failures) == 1
    assert result.triggered_failures[0].kind == "budget_exhausted"
