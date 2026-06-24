from __future__ import annotations

from openminion.modules.brain.loop.goals import (
    build_goal_iteration_report,
    report_goal_iteration,
    resolve_active_goal,
)
from openminion.modules.brain.schemas import BudgetCounters, Deliverable, Goal
from openminion.modules.brain.schemas import SuccessCriterion, WorkingState
from openminion.modules.brain.storage.goals import SQLiteGoalStore


def _goal(goal_id: str) -> Goal:
    return Goal(
        goal_id=goal_id,
        description=f"goal {goal_id}",
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
            )
        ],
    )


def _state(session_id: str, *, active_goal_id: str | None = None) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="agent-1",
        goal="legacy description",
        active_goal_id=active_goal_id,
        budgets_remaining=BudgetCounters(
            ticks=1,
            tool_calls=1,
            a2a_calls=0,
            tokens=1,
            time_ms=1,
        ),
    )


def test_resolve_active_goal_returns_none_for_goal_light_turn(tmp_path) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")
    store.create(_goal("goal-global"))

    assert resolve_active_goal(_state("sess-a"), goal_store=store) is None


def test_resolve_active_goal_uses_session_binding_not_global_list(tmp_path) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")
    store.create(_goal("goal-a"))
    store.create(_goal("goal-b"))
    store.bind_to_session("goal-a", "sess-a")
    store.bind_to_session("goal-b", "sess-b")

    resolved = resolve_active_goal(
        _state("sess-a", active_goal_id="goal-a"),
        goal_store=store,
    )
    cross_session = resolve_active_goal(
        _state("sess-a", active_goal_id="goal-b"),
        goal_store=store,
    )

    assert resolved is not None
    assert resolved.goal_id == "goal-a"
    assert cross_session is None


def test_goal_iteration_report_is_structural() -> None:
    report = build_goal_iteration_report(
        goal=_goal("goal-report"),
        outcome="advanced",
        reason="step completed",
        evidence_refs=[" artifact-1 ", ""],
    )

    assert report.goal_id == "goal-report"
    assert report.outcome == "advanced"
    assert report.evidence_refs == ("artifact-1",)


def test_report_goal_iteration_attaches_only_when_active_goal_exists(tmp_path) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")
    store.create(_goal("goal-a"))
    store.bind_to_session("goal-a", "sess-a")

    absent = report_goal_iteration(
        _state("sess-b"),
        outcome="blocked",
        goal_store=store,
    )
    present = report_goal_iteration(
        _state("sess-a", active_goal_id="goal-a"),
        outcome="satisfied",
        reason="criteria met",
        goal_store=store,
    )

    assert absent is None
    assert present is not None
    assert present.goal_id == "goal-a"
    assert present.outcome == "satisfied"
