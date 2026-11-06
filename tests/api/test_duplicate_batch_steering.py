"""SDK replay of duplicate-batch answer-only requests with queued steering."""

import pytest

from openminion.modules.brain.loop.adaptive.context import _AdaptiveLoopContextAdapter
from openminion.services.runtime.turn_input import TurnInputIntent
from tests.api.test_budget_steering import CORRECTION
from tests.api.test_text_answer_freshness import (
    FINALIZATION,
    NOT_TIME_SENSITIVE,
    Script,
    _all_text,
    _scripted_agent,
    _text,
    _tool_call,
)


class _DuplicateScript(Script):
    def __init__(self, arrival, enqueue):
        super().__init__(classifier=NOT_TIME_SENSITIVE, first_entry="tool")
        self.arrival = arrival
        self.enqueue = enqueue
        self.followups = []
        self.closeout = None

    def respond(self, body):
        text = _all_text(body)
        if "repeated an identical successful tool batch" in text:
            self.closeout = body
            return _text(f"ZEBRA-42\n\n{FINALIZATION}")
        choice = body.get("tool_choice")
        if not isinstance(choice, dict) and any(
            m.get("role") == "tool" and "ZEBRA-42" in str(m.get("content"))
            for m in body.get("messages", [])
        ):
            self.followups.append(body)
            if self.arrival == "pending" and len(self.followups) == 1:
                self.enqueue()
            return _tool_call(
                "lookup_order", {"order_id": "A-104"}, f"repeat-{len(self.followups)}"
            )
        return super().respond(body)


@pytest.mark.parametrize("arrival", ["none", "applied", "pending"])
def test_sdk_turn_duplicate_batch_closeout_preserves_steering(
    tmp_path, monkeypatch, arrival
):
    real = _AdaptiveLoopContextAdapter._postprocess_outcome
    queued = []
    scope = []

    def enqueue():
        queue, state = scope[0]
        entry = queue.enqueue(
            session_id=state.session_id,
            agent_id=state.agent_id,
            text=CORRECTION,
            intent=TurnInputIntent.STEER_CURRENT,
            steer_supported=True,
            target_trace_id=state.trace_id,
        )
        queued.append(entry)

    def postprocess(self, outcome, **kwargs):
        result = real(self, outcome, **kwargs)
        if (
            not scope
            and outcome.action_result
            and "ZEBRA-42" in str(outcome.action_result.outputs)
        ):
            scope.append((self._runner.turn_input_queue, self.state))
            if arrival == "applied":
                enqueue()
        return result

    monkeypatch.setattr(
        _AdaptiveLoopContextAdapter, "_postprocess_outcome", postprocess
    )
    script = _DuplicateScript(arrival, enqueue)
    with _scripted_agent(
        script, tmp_path, monkeypatch, forced_tools=[], max_calls_per_run=10
    ) as (agent, calls):
        result = agent.run(
            "Check order A-104", session_id="duplicate-steering", timeout_seconds=30
        )
    assert calls == ["A-104"]
    assert bool(script.followups)
    assert (CORRECTION in _all_text(script.followups[0])) == (arrival == "applied")
    assert script.closeout is not None
    closeout = script.closeout
    assert not closeout.get("tools")
    assert closeout.get("tool_choice", "none") == "none"
    assert "Check order A-104" in _all_text(closeout)
    assert "ZEBRA-42" in _all_text(closeout)
    assert _all_text(closeout).count(CORRECTION) == (arrival != "none")
    if arrival != "none":
        assert closeout["messages"][-1]["role"] == "user"
        assert closeout["messages"][-1]["content"] == CORRECTION
    queue, state = scope[0]
    entries = queue.list_entries(session_id=state.session_id)
    assert [entry.queue_id for entry in entries] == [entry.queue_id for entry in queued]
    assert all(entry.status.value == "completed" for entry in entries)
    assert "ZEBRA-42" in result.text
