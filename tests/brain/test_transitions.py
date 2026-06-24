from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openminion.modules.brain.constants import (
    BRAIN_STATE_ACTIVE,
    BRAIN_STATE_CONTINUE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_FAILED,
    BRAIN_STATE_JOB_PENDING,
    BRAIN_STATE_STOPPED,
    BRAIN_STATE_WAITING_USER,
    BRAIN_TERMINAL_STATES,
)
from openminion.modules.brain.diagnostics.transitions import (
    TRANSITIONS,
    IllegalTransitionError,
    allowed_events,
    set_status_unchecked,
    transition,
)

ALL_STATUSES = frozenset(
    {
        BRAIN_STATE_ACTIVE,
        BRAIN_STATE_CONTINUE,
        BRAIN_STATE_WAITING_USER,
        BRAIN_STATE_JOB_PENDING,
        BRAIN_STATE_DONE,
        BRAIN_STATE_ERROR,
        BRAIN_STATE_STOPPED,
        BRAIN_STATE_FAILED,
    }
)

NON_TERMINAL_STATUSES = ALL_STATUSES - BRAIN_TERMINAL_STATES


def _make_state(status: str) -> SimpleNamespace:
    return SimpleNamespace(status=status)


class TestTransitionTableProperties:
    def test_every_non_terminal_status_has_outbound(self) -> None:
        statuses_with_outbound = {src for src, _ in TRANSITIONS}
        for status in NON_TERMINAL_STATUSES:
            assert status in statuses_with_outbound, (
                f"Non-terminal status {status!r} has no outbound transition"
            )

    def test_terminal_statuses_have_no_outbound(self) -> None:
        for src, event in TRANSITIONS:
            assert src not in BRAIN_TERMINAL_STATES or src == BRAIN_STATE_DONE, (
                f"Terminal status {src!r} has outbound transition on "
                f"event {event!r} (only DONE may have closure transitions)"
            )

    def test_done_outbound_only_via_closure(self) -> None:
        for (src, event), _tgt in TRANSITIONS.items():
            if src == BRAIN_STATE_DONE:
                assert event.startswith("closure_"), (
                    f"DONE has non-closure outbound event {event!r}"
                )

    def test_no_unreachable_statuses(self) -> None:
        targets = {tgt for tgt in TRANSITIONS.values()}
        entry_statuses = {BRAIN_STATE_ACTIVE, BRAIN_STATE_CONTINUE}
        for status in ALL_STATUSES - entry_statuses:
            assert status in targets, (
                f"Status {status!r} is unreachable — never a transition target"
            )

    def test_all_keys_use_known_statuses(self) -> None:
        for (src, _event), tgt in TRANSITIONS.items():
            assert src in ALL_STATUSES, f"Unknown source status {src!r}"
            assert tgt in ALL_STATUSES, f"Unknown target status {tgt!r}"

    def test_no_duplicate_keys(self) -> None:
        keys = list(TRANSITIONS.keys())
        assert len(keys) == len(set(keys))

    def test_event_names_are_snake_case(self) -> None:
        for _src, event in TRANSITIONS:
            assert event == event.lower(), f"Event {event!r} is not lowercase"
            assert " " not in event, f"Event {event!r} contains spaces"
            assert event.replace("_", "").isalpha(), (
                f"Event {event!r} contains non-alpha chars"
            )


class TestTransitionFunction:
    def test_legal_transition_updates_status(self) -> None:
        state = _make_state(BRAIN_STATE_ACTIVE)
        transition(state, "task_completed")
        assert state.status == BRAIN_STATE_DONE

    def test_legal_transition_emits_event(self) -> None:
        state = _make_state(BRAIN_STATE_ACTIVE)
        logger = MagicMock()
        transition(state, "task_completed", logger=logger)
        logger.emit.assert_called_once_with(
            "brain.state.transition",
            {
                "from_status": BRAIN_STATE_ACTIVE,
                "to_status": BRAIN_STATE_DONE,
                "event": "task_completed",
            },
        )

    def test_legal_transition_no_event_without_logger(self) -> None:
        state = _make_state(BRAIN_STATE_ACTIVE)
        transition(state, "task_completed")
        assert state.status == BRAIN_STATE_DONE

    def test_illegal_transition_raises(self) -> None:
        state = _make_state(BRAIN_STATE_DONE)
        with pytest.raises(IllegalTransitionError) as exc_info:
            transition(state, "user_input_received")
        err = exc_info.value
        assert err.current == BRAIN_STATE_DONE
        assert err.event == "user_input_received"
        assert isinstance(err.allowed, list)

    def test_illegal_transition_does_not_change_status(self) -> None:
        state = _make_state(BRAIN_STATE_DONE)
        with pytest.raises(IllegalTransitionError):
            transition(state, "user_input_received")
        assert state.status == BRAIN_STATE_DONE

    def test_same_state_transition(self) -> None:
        state = _make_state(BRAIN_STATE_ACTIVE)
        transition(state, "step_advanced")
        assert state.status == BRAIN_STATE_ACTIVE

    def test_waiting_user_to_active(self) -> None:
        state = _make_state(BRAIN_STATE_WAITING_USER)
        transition(state, "user_input_received")
        assert state.status == BRAIN_STATE_ACTIVE

    def test_active_to_error(self) -> None:
        state = _make_state(BRAIN_STATE_ACTIVE)
        transition(state, "fatal_error")
        assert state.status == BRAIN_STATE_ERROR

    def test_job_pending_to_active(self) -> None:
        state = _make_state(BRAIN_STATE_JOB_PENDING)
        transition(state, "job_completed")
        assert state.status == BRAIN_STATE_ACTIVE

    def test_done_closure_replan(self) -> None:
        state = _make_state(BRAIN_STATE_DONE)
        transition(state, "closure_replan")
        assert state.status == BRAIN_STATE_ACTIVE

    def test_continue_next_tick(self) -> None:
        state = _make_state(BRAIN_STATE_CONTINUE)
        transition(state, "next_tick")
        assert state.status == BRAIN_STATE_ACTIVE


class TestSetStatusUnchecked:
    def test_sets_status_directly(self) -> None:
        state = _make_state(BRAIN_STATE_DONE)
        set_status_unchecked(state, BRAIN_STATE_ACTIVE, reason="bootstrap")
        assert state.status == BRAIN_STATE_ACTIVE

    def test_does_not_emit_event(self) -> None:
        state = _make_state(BRAIN_STATE_DONE)
        set_status_unchecked(state, BRAIN_STATE_ACTIVE, reason="test_fixture")
        assert state.status == BRAIN_STATE_ACTIVE

    def test_allows_any_status_value(self) -> None:
        state = _make_state(BRAIN_STATE_ERROR)
        set_status_unchecked(state, BRAIN_STATE_ACTIVE, reason="restore")
        assert state.status == BRAIN_STATE_ACTIVE


class TestAllowedEvents:
    def test_active_has_events(self) -> None:
        events = allowed_events(BRAIN_STATE_ACTIVE)
        assert "task_completed" in events
        assert "fatal_error" in events

    def test_terminal_error_has_no_events(self) -> None:
        events = allowed_events(BRAIN_STATE_ERROR)
        assert events == []

    def test_done_has_only_closure_events(self) -> None:
        events = allowed_events(BRAIN_STATE_DONE)
        assert all(e.startswith("closure_") for e in events)


class TestIllegalTransitionError:
    def test_diagnostic_message(self) -> None:
        err = IllegalTransitionError(
            current="done", event="bogus", allowed=["closure_replan"]
        )
        assert "done" in str(err)
        assert "bogus" in str(err)
        assert "closure_replan" in str(err)

    def test_attributes(self) -> None:
        err = IllegalTransitionError(current="done", event="bogus", allowed=["a", "b"])
        assert err.current == "done"
        assert err.event == "bogus"
        assert err.allowed == ["a", "b"]
