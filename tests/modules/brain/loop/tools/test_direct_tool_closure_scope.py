from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.schemas import ActionResult
from openminion.modules.brain.loop.tools.contracts import (
    AdaptiveToolLoopState,
    DirectToolTurnContext,
)
from openminion.modules.brain.loop.tools.direct_tool import (
    _direct_tool_batch_completed_successfully,
    _forced_tool_choice_for_direct_tool_turn,
    _should_force_direct_tool_closure,
)
from openminion.modules.llm.schemas import ToolCall, ToolSpec


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


def test_single_remaining_direct_tool_forces_provider_tool_choice() -> None:
    state = AdaptiveToolLoopState(
        direct_tool_turn=DirectToolTurnContext(
            requested_tool_names=("file.write",),
            requested_batch_signature="",
            match_by_name_only=True,
        ),
    )

    choice = _forced_tool_choice_for_direct_tool_turn(
        state,
        [ToolSpec(name="file.write")],
    )

    assert choice == "required"
    assert state.scratchpad["direct_tool_choice_forced"] == "file.write"


def test_direct_tool_choice_is_not_forced_for_multi_tool_sequences() -> None:
    state = AdaptiveToolLoopState(
        direct_tool_turn=DirectToolTurnContext(
            requested_tool_names=("file.write", "file.read"),
            requested_batch_signature="",
            match_by_name_only=True,
        ),
    )

    assert _forced_tool_choice_for_direct_tool_turn(state, []) is None


def test_name_only_direct_tool_counts_requested_tool_in_mixed_batch() -> None:
    state = AdaptiveToolLoopState(
        direct_tool_turn=DirectToolTurnContext(
            requested_tool_names=("file.write",),
            requested_batch_signature="",
            match_by_name_only=True,
        ),
    )
    ordered_results = [
        (
            SimpleNamespace(name="code.repo_index"),
            SimpleNamespace(
                approved_command=SimpleNamespace(tool_name="code.repo_index"),
                action_result=ActionResult(command_id="c1", status="success"),
            ),
        ),
        (
            SimpleNamespace(name="file.write"),
            SimpleNamespace(
                approved_command=SimpleNamespace(tool_name="file.write"),
                action_result=ActionResult(command_id="c2", status="success"),
            ),
        ),
    ]

    assert _direct_tool_batch_completed_successfully(
        loop_state=state,
        signature="",
        ordered_tool_results=ordered_results,
    )
    assert state.scratchpad["direct_tool_completed_tool_names"] == ["file.write"]
