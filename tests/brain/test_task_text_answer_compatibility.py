"""Plain answers do not authorize execution against an uncommitted task change."""

from unittest.mock import Mock

import pytest

from openminion.modules.brain.bootstrap.recovery import (
    _recover_simple_tool_parity_decision,
)
from openminion.modules.brain.loop.context.task_context import updated_task_context
from openminion.modules.brain.loop.context.task_entry import (
    apply_entry_task_context,
    entry_task_preflight,
)
from openminion.modules.brain.schemas import RespondDecision
from tests.brain.test_unified_turn_entry import _state, _text_response, _tool_response


@pytest.mark.parametrize("lifecycle", ["active", "paused", "completed"])
def test_plain_answer_preserves_task_and_blocks_autonomous_and_recovered_tools(
    lifecycle,
):
    state = _state()
    state.goal = "Export report"
    state.resolved_task_context = updated_task_context(
        None,
        {
            "operation": "start",
            "expected_revision": 0,
            "user_quote": "Export JSON",
            "objective": "Export report",
            "set_conditions": [{"key": "format", "quote": "JSON"}],
        },
        user_input="Export JSON",
    ).model_copy(update={"lifecycle": lifecycle})
    before = state.model_dump()
    api = Mock()
    response = _text_response("I can explain that.")
    result = apply_entry_task_context(
        state,
        response,
        user_input="What would CSV change?",
        clarify=False,
        logger=Mock(),
        session_api=api,
    )
    assert result is None
    assert state.resolved_task_context.model_dump() == before["resolved_task_context"]
    assert (
        state.goal == before["goal"]
        and state.task_workspace.model_dump() == before["task_workspace"]
    )
    api.append_event.assert_not_called()
    assert state.task_context_needs_update
    assert entry_task_preflight(state, has_new_input=False) is not None
    runner = Mock()
    recovered = _recover_simple_tool_parity_decision(
        runner=runner,
        state=state,
        user_input="tool time {}",
        capability_category="time",
        decision=RespondDecision(
            respond_kind="answer",
            reason_code="entry_text_response",
            answer="I can explain that.",
        ),
        response=response,
        logger=Mock(),
        llm_call_id="test",
    )
    assert recovered is None and not runner.mock_calls

    amendment = _tool_response(
        "respond",
        {
            "answer": "Changed to CSV",
            "task_update": {
                "operation": "update",
                "expected_revision": 1,
                "user_quote": "Use CSV",
                "set_conditions": [{"key": "format", "quote": "CSV"}],
            },
        },
    )
    assert (
        apply_entry_task_context(
            state, amendment, user_input="Use CSV", clarify=False, logger=Mock()
        )
        is None
    )
    assert state.resolved_task_context.conditions == {"format": "CSV"}
    assert state.resolved_task_context.revision == 2
    assert not state.task_context_needs_update
