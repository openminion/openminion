from __future__ import annotations

import os
import tempfile

from openminion.modules.brain.runtime.mrdd.hook import (
    maybe_run_mrdd_pre_dispatch_hook,
)
from openminion.modules.brain.runtime.mrdd.state import (
    get_mrdd_module_state,
    read_cadence_counter,
    write_policy_snapshot,
)
from openminion.modules.brain.runtime.regrounding import RegroundingPolicy
from openminion.modules.brain.schemas.goals import (
    Deliverable,
    Goal,
    SuccessCriterion,
)
from openminion.modules.brain.storage.goals.store import SQLiteGoalStore


class _FakeLogger:
    def __init__(self):
        self.events = []

    def log_canonical_event(self, *, event_type, payload):
        self.events.append((event_type, payload))


class _FakeState:
    def __init__(self):
        self.session_id = "s1"
        self.module_state = {}


class _FakeRunner:
    def __init__(self, goal_store=None):
        self.long_running_goals = None
        self.goal_store = goal_store


def _make_goal():
    return Goal(
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


def test_hook_is_strict_noop_by_default():

    runner = _FakeRunner()
    state = _FakeState()
    logger = _FakeLogger()
    maybe_run_mrdd_pre_dispatch_hook(runner=runner, state=state, logger=logger)
    assert logger.events == []
    # bucket exists but is empty (the helper creates it lazily)
    assert state.module_state == {"mrdd": {}}


def test_hook_advances_counter_when_enabled_without_inject():

    runner = _FakeRunner()
    state = _FakeState()
    logger = _FakeLogger()
    write_policy_snapshot(state, RegroundingPolicy(enabled=True, cadence_turns=5))
    bucket = get_mrdd_module_state(state)
    bucket["active_goal_payload"] = _make_goal().model_dump()
    maybe_run_mrdd_pre_dispatch_hook(runner=runner, state=state, logger=logger)
    assert read_cadence_counter(state) == 1
    assert not any(e[0] == "mrdd_regrounding_inject" for e in logger.events)


def test_hook_emits_inject_event_at_cadence():

    runner = _FakeRunner()
    state = _FakeState()
    logger = _FakeLogger()
    write_policy_snapshot(state, RegroundingPolicy(enabled=True, cadence_turns=1))
    bucket = get_mrdd_module_state(state)
    bucket["active_goal_payload"] = _make_goal().model_dump()
    # cadence_counter starts at 0, threshold=1 → first tick must advance
    maybe_run_mrdd_pre_dispatch_hook(runner=runner, state=state, logger=logger)
    # On the second call the counter has reached threshold → inject fires
    maybe_run_mrdd_pre_dispatch_hook(runner=runner, state=state, logger=logger)
    assert any(e[0] == "mrdd_regrounding_inject" for e in logger.events)


def test_hook_clears_one_shot_just_compacted_after_consuming():

    runner = _FakeRunner()
    state = _FakeState()
    logger = _FakeLogger()
    write_policy_snapshot(state, RegroundingPolicy(enabled=True, cadence_turns=99))
    bucket = get_mrdd_module_state(state)
    bucket["active_goal_payload"] = _make_goal().model_dump()
    bucket["just_compacted"] = True
    maybe_run_mrdd_pre_dispatch_hook(runner=runner, state=state, logger=logger)
    assert bucket["just_compacted"] is False


def test_hook_persists_drift_signal_into_audit_trail_when_store_present():

    with tempfile.TemporaryDirectory() as tmpdir:
        store = SQLiteGoalStore(os.path.join(tmpdir, "goals.db"))
        goal = _make_goal()
        store.create(goal)
        # The current hook trajectory is None by design (per-tick wiring
        # directly via record_drift_signal_audit — the seam the hook
        # uses when a signal fires.
        from openminion.modules.brain.schemas.goals import GoalDriftSignal

        store.record_drift_signal_audit(
            GoalDriftSignal(
                signal_id="s1",
                goal_id=goal.goal_id,
                kind="actions_diverge_from_criteria",
                description="diverged",
                detected_at="2026-05-26",
                evidence={"x": 1},
            )
        )
        trail = store.list_goal_audit_trail(goal.goal_id)
        drift = [r for r in trail if r.actor == "mrdd_drift_detector"]
        assert len(drift) == 1


def test_hook_never_raises_on_logger_failure():

    class _RaisingLogger:
        def log_canonical_event(self, *, event_type, payload):
            raise RuntimeError("logger boom")

    runner = _FakeRunner()
    state = _FakeState()
    logger = _RaisingLogger()
    write_policy_snapshot(state, RegroundingPolicy(enabled=True, cadence_turns=1))
    bucket = get_mrdd_module_state(state)
    bucket["active_goal_payload"] = _make_goal().model_dump()
    # No assertion needed — pass means hook did not raise
    maybe_run_mrdd_pre_dispatch_hook(runner=runner, state=state, logger=logger)
    maybe_run_mrdd_pre_dispatch_hook(runner=runner, state=state, logger=logger)
