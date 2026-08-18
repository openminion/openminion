from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openminion.modules.brain.constants import BRAIN_STATE_DONE
from openminion.modules.brain.execution.runtime.turn.dispatch import (
    _dispatch_runtime_error,
    dispatch,
)
from openminion.modules.brain.execution.runtime.turn.recursive import (
    _recursive_runtime_error,
)
from openminion.modules.brain.schemas import BudgetCounters, WorkingState
from openminion.modules.llm.providers.base import ProviderError


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


def test_dispatch_propagates_only_empty_provider_response_error() -> None:
    exhausted = ProviderError(
        "empty after retry",
        code="EMPTY_PROVIDER_RESPONSE",
    )
    with (
        patch(
            "openminion.modules.brain.execution.runtime.turn.dispatch._disabled_handoff_wait_response",
            side_effect=exhausted,
        ),
        pytest.raises(ProviderError) as raised,
    ):
        dispatch(
            runner=SimpleNamespace(profile=None),
            state=_state(),
            logger=MagicMock(),
            request=SimpleNamespace(
                consume_user_input_for_command=False,
                user_input="hello",
            ),
        )

    assert raised.value is exhausted


def test_dispatch_keeps_other_provider_errors_on_existing_runtime_error_path() -> None:
    converted = SimpleNamespace(status="error")
    with (
        patch(
            "openminion.modules.brain.execution.runtime.turn.dispatch._disabled_handoff_wait_response",
            side_effect=ProviderError("ordinary failure", code="PROVIDER_ERROR"),
        ),
        patch(
            "openminion.modules.brain.execution.runtime.turn.dispatch._dispatch_runtime_error",
            return_value=converted,
        ) as runtime_error,
    ):
        result = dispatch(
            runner=SimpleNamespace(profile=None),
            state=_state(),
            logger=MagicMock(),
            request=SimpleNamespace(
                consume_user_input_for_command=False,
                user_input="hello",
            ),
        )

    assert result is converted
    runtime_error.assert_called_once()
