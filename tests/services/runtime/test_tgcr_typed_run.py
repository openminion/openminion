from __future__ import annotations

import pytest

from openminion.services.runtime.run_status import (
    RUN_STATE_COMPLETED,
    RUN_STATE_FAILED,
    RUN_STATE_RUNNING,
    RUN_TERMINAL_BLOCKED,
    RUN_TERMINAL_BUDGET_EXHAUSTED,
    RUN_TERMINAL_COMPLETED,
    RUN_TERMINAL_FAILED,
    RUN_TERMINAL_NEEDS_HUMAN,
    Run,
    RunCheckpoint,
    is_run_terminal_state,
    resolve_run_terminal_persistence,
)


class TestRunTerminalState:
    def test_five_known_terminal_values(self) -> None:
        assert RUN_TERMINAL_COMPLETED == "completed"
        assert RUN_TERMINAL_FAILED == "failed"
        assert RUN_TERMINAL_BLOCKED == "blocked"
        assert RUN_TERMINAL_NEEDS_HUMAN == "needs_human"
        assert RUN_TERMINAL_BUDGET_EXHAUSTED == "budget_exhausted"

    @pytest.mark.parametrize(
        "value",
        [
            "completed",
            "failed",
            "blocked",
            "needs_human",
            "budget_exhausted",
        ],
    )
    def test_is_run_terminal_state_true(self, value: str) -> None:
        assert is_run_terminal_state(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "running",
            "queued",
            "waiting_tool",
            "verifier_disagreed",  # TGCR-Q3: NOT a separate terminal
            None,
            123,
        ],
    )
    def test_is_run_terminal_state_false(self, value: object) -> None:
        assert is_run_terminal_state(value) is False


class TestResolveRunTerminalPersistence:
    def test_completed_persists_as_run_state_completed(self) -> None:
        assert resolve_run_terminal_persistence("completed") == RUN_STATE_COMPLETED

    @pytest.mark.parametrize(
        "terminal",
        [
            "failed",
            "blocked",
            "needs_human",
            "budget_exhausted",
        ],
    )
    def test_non_completed_persists_as_run_state_failed(self, terminal: str) -> None:
        # TGCR design: the persisted event vocabulary stays unchanged
        # (run.completed / run.failed); the typed terminal value carries
        # the finer-grained reason on the payload.
        assert resolve_run_terminal_persistence(terminal) == RUN_STATE_FAILED

    def test_unknown_terminal_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown RunTerminalState"):
            resolve_run_terminal_persistence("verifier_disagreed")


class TestRunDataclass:
    def test_minimal_run_construction(self) -> None:
        run = Run(
            run_id="r1",
            session_id="s1",
            goal_id="g1",
            state=RUN_STATE_RUNNING,
        )
        assert run.run_id == "r1"
        assert run.state == RUN_STATE_RUNNING
        assert run.is_terminal() is False
        assert run.terminal_state is None
        assert run.apd_plan_id is None

    def test_terminal_run_has_terminal_state(self) -> None:
        run = Run(
            run_id="r1",
            session_id="s1",
            goal_id="g1",
            state=RUN_STATE_COMPLETED,
            terminal_state=RUN_TERMINAL_COMPLETED,
        )
        assert run.is_terminal() is True
        assert run.terminal_state == RUN_TERMINAL_COMPLETED

    def test_to_dict_omits_none_optionals(self) -> None:
        payload = Run(
            run_id="r1",
            session_id="s1",
            goal_id="g1",
            state=RUN_STATE_RUNNING,
        ).to_dict()
        assert payload == {
            "run_id": "r1",
            "session_id": "s1",
            "goal_id": "g1",
            "state": RUN_STATE_RUNNING,
        }

    def test_to_dict_includes_terminal_and_plan(self) -> None:
        payload = Run(
            run_id="r1",
            session_id="s1",
            goal_id="g1",
            state=RUN_STATE_FAILED,
            terminal_state=RUN_TERMINAL_BUDGET_EXHAUSTED,
            apd_plan_id="plan-7",
        ).to_dict()
        assert payload["terminal_state"] == RUN_TERMINAL_BUDGET_EXHAUSTED
        assert payload["apd_plan_id"] == "plan-7"


class TestRunCheckpoint:
    def test_typed_checkpoint_construction(self) -> None:
        cp = RunCheckpoint(
            checkpoint_id="cp-1",
            run_id="r1",
            goal_id="g1",
            sequence=3,
            state_snapshot={"current_step": "exec.tool"},
            created_at="2026-05-14T00:00:00Z",
        )
        assert cp.sequence == 3
        assert cp.state_snapshot == {"current_step": "exec.tool"}
        payload = cp.to_dict()
        # to_dict copies the snapshot so callers can't mutate the frozen
        # record's state.
        assert payload["state_snapshot"] == {"current_step": "exec.tool"}
        payload["state_snapshot"]["mutated"] = True
        assert cp.state_snapshot == {"current_step": "exec.tool"}

    def test_default_snapshot_is_empty_dict(self) -> None:
        cp = RunCheckpoint(
            checkpoint_id="cp-1",
            run_id="r1",
            goal_id="g1",
            sequence=0,
        )
        assert cp.state_snapshot == {}
