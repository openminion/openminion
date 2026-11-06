"""SDK turn with a scripted provider and steering during tool execution."""

from openminion.modules.brain.loop.adaptive.context import _AdaptiveLoopContextAdapter
from openminion.services.runtime.turn_input import TurnInputIntent
from tests.api.test_text_answer_freshness import (
    FINALIZATION,
    NOT_TIME_SENSITIVE,
    Script,
    _all_text,
    _scripted_agent,
    _text,
)

CORRECTION = "Keep the order ID, but report only the pickup code."


class _BudgetScript(Script):
    def __init__(self):
        super().__init__(classifier=NOT_TIME_SENSITIVE, first_entry="tool")
        self.closeout = None

    def respond(self, body):
        if "The tool budget or a per-tool limit has been reached." in _all_text(body):
            self.closeout = body
            return _text(f"ZEBRA-42\n\n{FINALIZATION}")
        return super().respond(body)


def test_sdk_turn_delivers_pending_steering_at_budget_closeout(tmp_path, monkeypatch):
    script = _BudgetScript()
    original = _AdaptiveLoopContextAdapter._postprocess_outcome
    enqueued = []

    def postprocess(self, outcome, **kwargs):
        result = original(self, outcome, **kwargs)
        if outcome.action_result and "ZEBRA-42" in str(outcome.action_result.outputs):
            state = self.state
            queue = self._runner.turn_input_queue
            entry = queue.enqueue(
                session_id=state.session_id,
                agent_id=state.agent_id,
                text=CORRECTION,
                intent=TurnInputIntent.STEER_CURRENT,
                steer_supported=True,
                target_trace_id=state.trace_id,
            )
            enqueued.append((queue, entry))
        return result

    monkeypatch.setattr(
        _AdaptiveLoopContextAdapter, "_postprocess_outcome", postprocess
    )
    with _scripted_agent(
        script, tmp_path, monkeypatch, forced_tools=[], max_calls_per_run=1
    ) as (agent, calls):
        result = agent.run(
            "Check order A-104", session_id="budget-steering", timeout_seconds=30
        )

    assert calls == ["A-104"]
    assert len(enqueued) == 1
    assert script.closeout is not None, script.kinds
    assert script.closeout.get("tool_choice", "none") == "none"
    assert not script.closeout.get("tools")
    assert _all_text(script.closeout).count(CORRECTION) == 1
    assert script.closeout["messages"][-1]["role"] == "user"
    assert CORRECTION in _all_text({"messages": script.closeout["messages"][-1:]})
    assert "Check order A-104" in _all_text(script.closeout)
    assert "ZEBRA-42" in result.text
    queue, entry = enqueued[0]
    completed = queue.list_entries(session_id=entry.session_id)
    assert [(item.queue_id, item.status.value) for item in completed] == [
        (entry.queue_id, "completed")
    ]
