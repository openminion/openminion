from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openminion.modules.brain.constants import BRAIN_STATE_DONE
from openminion.modules.brain.execution.runtime.turn.dispatch import (
    _dispatch_runtime_error,
)
from openminion.modules.brain.execution.runtime.turn.recursive import (
    _recursive_runtime_error,
)
from openminion.modules.brain.schemas import BudgetCounters, WorkingState


def _state() -> WorkingState:
    state = WorkingState(
        session_id="s-terminal-error",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=1,
            tool_calls=1,
            a2a_calls=0,
            tokens=100,
            time_ms=1000,
        ),
    )
    state.status = BRAIN_STATE_DONE
    return state


def test_dispatch_runtime_error_does_not_retransition_done_state() -> None:
    state = _state()
    logger = MagicMock()
    with patch(
        "openminion.modules.brain.execution.runtime.turn.dispatch._runner_delegate",
        return_value=SimpleNamespace(status="error"),
    ):
        _dispatch_runtime_error(
            runner=SimpleNamespace(),
            state=state,
            logger=logger,
            user_input=None,
            exc=RuntimeError("late failure"),
        )

    assert state.status == BRAIN_STATE_DONE
    assert not any(
        call.args and call.args[0] == "brain.state.transition"
        for call in logger.emit.call_args_list
    )


def test_recursive_runtime_error_does_not_retransition_done_state() -> None:
    state = _state()
    logger = MagicMock()
    with patch(
        "openminion.modules.brain.execution.runtime.turn.recursive._runner_delegate",
        return_value=SimpleNamespace(status="error"),
    ):
        _recursive_runtime_error(
            runner=SimpleNamespace(rlm_api=SimpleNamespace(recursive_source="test")),
            state=state,
            logger=logger,
            exc=RuntimeError("late recursive failure"),
        )

    assert state.status == BRAIN_STATE_DONE
    assert not any(
        call.args and call.args[0] == "brain.state.transition"
        for call in logger.emit.call_args_list
    )
