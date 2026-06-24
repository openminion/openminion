from __future__ import annotations

from openminion.modules.brain.loop.tools.contracts import (
    AdaptiveToolLoopState,
    DirectToolTurnContext,
)
from openminion.modules.brain.loop.tools.direct_tool import (
    _should_force_direct_tool_closure,
)
from openminion.modules.llm.schemas import ToolCall


def _state_for_requested_tools(*tool_names: str) -> AdaptiveToolLoopState:
    calls = tuple(ToolCall(name=name, arguments={}) for name in tool_names)
    return AdaptiveToolLoopState(
        direct_tool_turn=DirectToolTurnContext(
            requested_tool_names=tuple(tool_names),
            requested_batch_signature="seeded-batch",
            requested_calls=calls,
        ),
        direct_tool_requested_batch_satisfied=True,
    )


def test_single_direct_tool_batch_still_forces_answer_only_closure() -> None:
    state = _state_for_requested_tools("web.fetch")

    assert _should_force_direct_tool_closure(state) is True


def test_multi_tool_seeded_batch_continues_through_normal_tool_loop() -> None:
    state = _state_for_requested_tools("web.fetch", "file.read", "file.read")

    assert _should_force_direct_tool_closure(state) is False


def test_consumed_direct_tool_closure_does_not_force_again() -> None:
    state = _state_for_requested_tools("web.fetch")
    state.direct_tool_closure_consumed = True

    assert _should_force_direct_tool_closure(state) is False
