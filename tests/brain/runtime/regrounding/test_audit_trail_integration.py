from __future__ import annotations

from pathlib import Path

from openminion.modules.brain.schemas.goals import (
    Deliverable,
    Goal,
    GoalDriftSignal,
    SuccessCriterion,
)
from openminion.modules.brain.storage.goals.store import SQLiteGoalStore


def _seed_goal(store):
    g = Goal(
        goal_id="g-1",
        description="Ship MRDD",
        success_criteria=[
            SuccessCriterion(
                criterion_id="c1",
                description="lands",
                structural_check="regrounding.module_exists",
            ),
        ],
        deliverables=[Deliverable(deliverable_id="d1", description="slice")],
    )
    store.create(g)
    return g


def _store(tmp_path: Path) -> SQLiteGoalStore:
    return SQLiteGoalStore(str(tmp_path / "g.db"))


def _make_signal(**overrides):
    payload = dict(
        signal_id="sig-1",
        goal_id="g-1",
        kind="actions_diverge_from_criteria",
        description="actions diverged",
        detected_at="2026-05-26T00:00:00Z",
        evidence={"recent_action_count": 5},
    )
    payload.update(overrides)
    return GoalDriftSignal(**payload)


def test_drift_signal_audit_writes_one_row_with_mrdd_actor(tmp_path: Path):
    store = _store(tmp_path)
    _seed_goal(store)
    store.record_drift_signal_audit(_make_signal())
    trail = store.list_goal_audit_trail("g-1")
    drift = [r for r in trail if r.actor == "mrdd_drift_detector"]
    assert len(drift) == 1


def test_drift_signal_audit_carries_kind_in_reason(tmp_path: Path):
    store = _store(tmp_path)
    _seed_goal(store)
    store.record_drift_signal_audit(_make_signal(kind="inaction_against_criteria"))
    trail = store.list_goal_audit_trail("g-1")
    drift = [r for r in trail if r.actor == "mrdd_drift_detector"]
    assert drift[0].reason.startswith("mrdd_drift:inaction_against_criteria:")


def test_drift_signal_audit_round_trips_full_evidence(tmp_path: Path):
    store = _store(tmp_path)
    _seed_goal(store)
    store.record_drift_signal_audit(
        _make_signal(evidence={"recent_action_count": 5, "matched_token_count": 0})
    )
    trail = store.list_goal_audit_trail("g-1")
    drift = [r for r in trail if r.actor == "mrdd_drift_detector"][0]
    assert drift.action_authorization["signal_id"] == "sig-1"
    assert drift.action_authorization["kind"] == "actions_diverge_from_criteria"
    assert drift.action_authorization["evidence"]["recent_action_count"] == 5


def test_drift_signal_audit_does_not_transition_goal_status(tmp_path: Path):
    store = _store(tmp_path)
    _seed_goal(store)
    store.record_drift_signal_audit(_make_signal())
    trail = store.list_goal_audit_trail("g-1")
    drift = [r for r in trail if r.actor == "mrdd_drift_detector"][0]
    assert drift.prior_status is None
    assert drift.new_status is None
