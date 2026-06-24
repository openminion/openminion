from __future__ import annotations

from openminion.modules.brain.constants import MissionStatus
from openminion.modules.brain.schemas import (
    Deliverable,
    ExternalBlocker,
    FailureCondition,
    Goal,
    SuccessCriterion,
)
from openminion.modules.brain.storage.goals import SQLiteGoalStore


def _goal(
    goal_id: str,
    *,
    parent_goal_id: str | None = None,
) -> Goal:
    return Goal(
        goal_id=goal_id,
        description=f"goal {goal_id}",
        success_criteria=[
            SuccessCriterion(
                criterion_id=f"{goal_id}-criterion",
                description="criterion",
                structural_check="artifact_present",
            )
        ],
        deliverables=[
            Deliverable(
                deliverable_id=f"{goal_id}-deliverable",
                description="deliverable",
            )
        ],
        parent_goal_id=parent_goal_id,
    )


def test_goal_store_round_trips_create_transition_and_plan_link(tmp_path) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")

    created = store.create(_goal("goal-root"))
    fetched = store.get("goal-root")

    assert fetched is not None
    assert fetched.goal_id == created.goal_id
    assert fetched.status == MissionStatus.ACTIVE

    transitioned = store.transition_status(
        "goal-root",
        MissionStatus.PAUSED,
        reason="operator pause",
    )
    assert transitioned.status == MissionStatus.PAUSED
    assert store.get("goal-root") is not None
    assert store.get("goal-root").status == MissionStatus.PAUSED  # type: ignore[union-attr]

    linked = store.set_apd_plan_id("goal-root", "plan-7")
    assert linked.apd_plan_id == "plan-7"
    assert store.get("goal-root").apd_plan_id == "plan-7"  # type: ignore[union-attr]
    assert [goal.goal_id for goal in store.list_by_plan_id("plan-7")] == ["goal-root"]


def test_goal_store_lists_active_and_parent_children(tmp_path) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")

    parent = store.create(_goal("goal-parent"))
    store.create(_goal("goal-child-a", parent_goal_id=parent.goal_id))
    child_b = store.create(_goal("goal-child-b", parent_goal_id=parent.goal_id))
    store.create(_goal("goal-unrelated"))
    store.transition_status(child_b.goal_id, MissionStatus.COMPLETED, reason="done")

    active_ids = [goal.goal_id for goal in store.list_active()]
    child_ids = [goal.goal_id for goal in store.list_by_parent(parent.goal_id)]

    assert "goal-parent" in active_ids
    assert "goal-child-a" in active_ids
    assert "goal-child-b" not in active_ids
    assert child_ids == ["goal-child-b", "goal-child-a"]


def test_goal_store_lists_active_goals_by_session(tmp_path) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")
    store.create(_goal("goal-a"))
    store.create(_goal("goal-b"))
    store.create(_goal("goal-paused"))
    store.bind_to_session("goal-a", "sess-a")
    store.bind_to_session("goal-b", "sess-b")
    store.bind_to_session("goal-paused", "sess-a")
    store.pause("goal-paused", reason="wait")

    assert [goal.goal_id for goal in store.list_active_for_session("sess-a")] == [
        "goal-paused"
    ]
    assert [goal.goal_id for goal in store.list_active_for_session("sess-b")] == [
        "goal-b"
    ]
    assert store.is_bound_to_session("goal-a", "sess-a") is False
    assert store.is_bound_to_session("goal-paused", "sess-a") is True
    assert store.is_bound_to_session("goal-a", "sess-b") is False


def test_goal_store_keeps_one_active_goal_binding_per_session(tmp_path) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")
    store.create(_goal("goal-old"))
    store.create(_goal("goal-new"))

    store.bind_to_session("goal-old", "sess-a")
    store.bind_to_session("goal-new", "sess-a")

    assert [goal.goal_id for goal in store.list_active_for_session("sess-a")] == [
        "goal-new"
    ]


def test_goal_store_rejects_illegal_status_transition(tmp_path) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")
    store.create(_goal("goal-terminal"))
    store.transition_status("goal-terminal", MissionStatus.COMPLETED, reason="done")

    try:
        store.transition_status("goal-terminal", MissionStatus.ACTIVE, reason="retry")
    except ValueError as exc:
        assert "Illegal Goal.status transition" in str(exc)
    else:
        raise AssertionError("expected invalid transition to raise ValueError")


def test_goal_store_records_audit_blockers_and_transfer(tmp_path) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")
    store.create(_goal("goal-audit"))

    store.pause("goal-audit", reason="manual_pause")
    store.add_external_blocker(
        "goal-audit",
        ExternalBlocker(
            blocker_id="blk-1",
            kind="human_approval",
            descriptor="approve before continue",
            created_at="2026-05-24T00:00:00Z",
        ),
    )
    store.clear_external_blocker("goal-audit", "blk-1")
    store.transfer_owner(
        "goal-audit",
        from_agent="agent-a",
        to_agent="agent-b",
        reason="handoff",
    )

    goal = store.get("goal-audit")
    assert goal is not None
    assert goal.owner_agent_id == "agent-b"
    assert goal.external_blockers == []

    audit = store.list_goal_audit_trail("goal-audit")
    assert len(audit) >= 2
    assert audit[0].entity_kind == "goal"
    assert any(row.reason == "manual_pause" for row in audit)
    assert any(row.action_authorization.get("transfer_owner") is True for row in audit)


def test_goal_store_replace_persists_failure_condition_and_audits_status_change(
    tmp_path,
) -> None:
    store = SQLiteGoalStore(tmp_path / "goals.db")
    created = store.create(_goal("goal-replace"))
    updated = created.model_copy(
        update={
            "status": MissionStatus.HALTED,
            "failure_conditions": [
                *created.failure_conditions,
                FailureCondition(
                    condition_id="budget",
                    kind="budget_exhausted",
                    description="budget hit",
                ),
            ],
        }
    )

    persisted = store.replace(updated, reason="cost_budget")

    assert persisted.status == MissionStatus.HALTED
    assert persisted.failure_conditions[-1].kind == "budget_exhausted"
    audit = store.list_goal_audit_trail("goal-replace")
    assert audit[-1].prior_status == MissionStatus.ACTIVE.value
    assert audit[-1].new_status == MissionStatus.HALTED.value
    assert audit[-1].reason == "cost_budget"
