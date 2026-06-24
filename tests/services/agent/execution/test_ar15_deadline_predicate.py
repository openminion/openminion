from __future__ import annotations

import time

from openminion.services.agent.execution.deadlines import (
    AR15_VOCABULARY_VALUE,
    DeadlineState,
    build_time_budget_exceeded_metadata,
    elapsed_ms,
    is_deadline_exceeded,
    start_deadline,
)


def test_vocabulary_value_matches_ar12_constant() -> None:
    assert AR15_VOCABULARY_VALUE == "time_budget_exceeded"


def test_fresh_deadline_not_exceeded() -> None:
    state = start_deadline(max_elapsed_ms=5000)
    assert is_deadline_exceeded(state) is False


def test_deadline_exceeded_after_budget() -> None:
    state = start_deadline(max_elapsed_ms=10)  # 10 ms budget
    time.sleep(0.05)  # 50 ms wait
    assert is_deadline_exceeded(state) is True


def test_none_state_treated_as_no_deadline() -> None:
    assert is_deadline_exceeded(None) is False


def test_zero_budget_treated_as_no_deadline() -> None:
    state = start_deadline(max_elapsed_ms=0)
    assert is_deadline_exceeded(state) is False


def test_negative_budget_treated_as_no_deadline() -> None:
    state = DeadlineState(started_at_ms=0.0, max_elapsed_ms=-100)
    assert is_deadline_exceeded(state) is False


def test_elapsed_ms_returns_zero_for_none_state() -> None:
    assert elapsed_ms(None) == 0


def test_elapsed_ms_grows_monotonically() -> None:
    state = start_deadline(max_elapsed_ms=10000)
    first = elapsed_ms(state)
    time.sleep(0.02)
    second = elapsed_ms(state)
    assert second >= first


def test_typed_metadata_uses_vocabulary_value() -> None:
    state = start_deadline(max_elapsed_ms=1000)
    metadata = build_time_budget_exceeded_metadata(state)
    assert metadata["tool_loop_termination_reason"] == AR15_VOCABULARY_VALUE
    assert metadata["time_budget_ms"] == "1000"
    assert int(metadata["elapsed_ms"]) >= 0


def test_typed_metadata_handles_none_state() -> None:
    metadata = build_time_budget_exceeded_metadata(None)
    assert metadata["tool_loop_termination_reason"] == "time_budget_exceeded"
    assert metadata["elapsed_ms"] == "0"
    assert metadata["time_budget_ms"] == "0"


def test_deadline_state_is_frozen() -> None:
    state = start_deadline(max_elapsed_ms=1000)
    try:
        state.started_at_ms = 0.0  # type: ignore[misc]
    except Exception:
        return
    assert False, "DeadlineState must be frozen"
