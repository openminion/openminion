from __future__ import annotations

import os
import tempfile

from openminion.modules.brain.runtime.drift import (
    ActionTrajectoryRecord,
    detect_goal_drift,
)
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


class _SmokeLogger:
    def __init__(self):
        self.events = []

    def log_canonical_event(self, *, event_type, payload):
        self.events.append((event_type, payload))


class _SmokeRunner:
    def __init__(self, goal_store):
        self.long_running_goals = None
        self.goal_store = goal_store


class _SmokeState:
    def __init__(self):
        self.session_id = "smoke-session"
        self.module_state = {}


def test_e2e_smoke_goal_to_drift_to_audit_trail_to_operator_surface():

    with tempfile.TemporaryDirectory() as tmpdir:
        # Surface 1: goal store + typed Goal lands.
        store = SQLiteGoalStore(os.path.join(tmpdir, "goals.db"))
        goal = Goal(
            goal_id="smoke-goal",
            description="Validate MRDD end-to-end",
            success_criteria=[
                SuccessCriterion(
                    criterion_id="c1",
                    description="all 4 closed-set drift kinds detectable",
                    structural_check="regrounding.module_exists",
                ),
                SuccessCriterion(
                    criterion_id="c2",
                    description="regression slice green",
                    structural_check="regrounding.tests_pass",
                ),
            ],
            deliverables=[
                Deliverable(deliverable_id="d1", description="smoke evidence")
            ],
        )
        store.create(goal)

        # Surface 2: hook fires the inject side under an enabled policy.
        runner = _SmokeRunner(goal_store=store)
        state = _SmokeState()
        logger = _SmokeLogger()
        write_policy_snapshot(state, RegroundingPolicy(enabled=True, cadence_turns=1))
        bucket = get_mrdd_module_state(state)
        bucket["active_goal_payload"] = goal.model_dump()

        # Tick once → counter 0 → 1.
        maybe_run_mrdd_pre_dispatch_hook(runner=runner, state=state, logger=logger)
        # Tick again → counter 1 ≥ threshold 1 → inject fires.
        maybe_run_mrdd_pre_dispatch_hook(runner=runner, state=state, logger=logger)
        inject_events = [e for e in logger.events if e[0] == "mrdd_regrounding_inject"]
        assert inject_events, "expected an mrdd_regrounding_inject event"
        # Counter reset to 0 post-inject.
        assert read_cadence_counter(state) == 0

        # Surface 3: drift detector emits typed signal on divergent trajectory.
        traj = ActionTrajectoryRecord(
            recent_action_tokens=("unrelated_a", "unrelated_b", "unrelated_c")
        )
        signal = detect_goal_drift(
            goal=goal,
            trajectory=traj,
            detected_at="2026-05-26T00:00:00Z",
            signal_id="smoke-sig-1",
        )
        assert signal is not None
        assert signal.kind == "actions_diverge_from_criteria"

        # Surface 4: signal persisted into LGMH-17 audit trail.
        store.record_drift_signal_audit(signal)

        # Surface 5: operator-facing read back via list_goal_audit_trail.
        trail = store.list_goal_audit_trail("smoke-goal")
        drift_rows = [r for r in trail if r.actor == "mrdd_drift_detector"]
        assert len(drift_rows) == 1
        op_row = drift_rows[0]
        assert op_row.entity_id == "smoke-goal"
        assert op_row.reason.startswith("mrdd_drift:actions_diverge_from_criteria:")
        # Operator can pull the structural evidence verbatim.
        assert op_row.action_authorization["signal_id"] == "smoke-sig-1"
        assert op_row.action_authorization["kind"] == "actions_diverge_from_criteria"
        # Drift is non-status-transitioning.
        assert op_row.prior_status is None
        assert op_row.new_status is None
