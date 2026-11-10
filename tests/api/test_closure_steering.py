"""Closure judge steering through the SDK and OpenAI-compatible transport."""

import json

import pytest

from openminion.modules.brain.loop.adaptive.context import _AdaptiveLoopContextAdapter
from openminion.services.runtime.turn_input import TurnInputIntent
from tests.api.test_text_answer_freshness import (
    CODE,
    FINALIZATION,
    JUDGE_TEXT,
    NOT_TIME_SENSITIVE,
    Script,
    _all_text,
    _scripted_agent,
    _text,
    _tool_call,
)

GOAL = "Check order A-104. Preserve the order ID and answer in English."
CORRECTIONS = [
    "Report the pickup code and the order status.",
    "Omit the order status. Keep the pickup code.",
]
EXPECTED = f"A-104: {CODE}"
STEERING_LABEL = "applied_turn_steering: "


def _judge_steering(text):
    return [
        json.loads(line.removeprefix(STEERING_LABEL))
        for line in text.splitlines()
        if line.startswith(STEERING_LABEL)
    ]


class _ClosureScript(Script):
    def __init__(self):
        super().__init__(classifier=NOT_TIME_SENSITIVE, first_entry="tool")
        self.drafts = []
        self.judges = []

    def respond(self, body):
        text = _all_text(body)
        if "[JUDGE CONTEXT]" in text:
            self.judges.append(body)
            answer = EXPECTED if _judge_steering(text) == [CORRECTIONS] else JUDGE_TEXT
            return _tool_call(
                "submit_output",
                {"final_answer": answer, "satisfied": True, "next_action": "close"},
                f"steering-judge-{len(self.judges)}",
            )
        if "The tool budget or a per-tool limit has been reached." in text or any(
            message.get("role") == "tool" for message in body["messages"]
        ):
            self.drafts.append(body)
            # The first draft still needs the order ID. Exercise the judge's
            # ability to improve it while preserving the steering.
            draft = CODE if len(self.drafts) == 1 else JUDGE_TEXT
            return _text(f"{draft}\n\n{FINALIZATION}")
        return super().respond(body)


@pytest.mark.parametrize("tool_cap", [1, None], ids=["budget", "ordinary"])
def test_sdk_closure_uses_ordered_current_turn_steering(
    tmp_path, monkeypatch, tool_cap
):
    script = _ClosureScript()
    original_postprocess = _AdaptiveLoopContextAdapter._postprocess_outcome
    original_apply = _AdaptiveLoopContextAdapter.apply_turn_steering
    enqueued = []
    other_trace_text = "A completed correction from another turn."

    def postprocess(self, outcome, **kwargs):
        result = original_postprocess(self, outcome, **kwargs)
        if not outcome.action_result or CODE not in str(outcome.action_result.outputs):
            return result
        state = self.state
        if not enqueued:
            queue = self._runner.turn_input_queue
            other = queue.enqueue(
                session_id=state.session_id,
                agent_id=state.agent_id,
                text=other_trace_text,
                intent=TurnInputIntent.STEER_CURRENT,
                steer_supported=True,
                target_trace_id="other-trace",
            )
            queue.reserve_steering(
                session_id=state.session_id,
                agent_id=state.agent_id,
                target_trace_id="other-trace",
            )
            queue.complete_steering(
                queue_ids=[other.queue_id], target_trace_id="other-trace"
            )
            entry = queue.enqueue(
                session_id=state.session_id,
                agent_id=state.agent_id,
                text=CORRECTIONS[0],
                intent=TurnInputIntent.STEER_CURRENT,
                steer_supported=True,
                target_trace_id=state.trace_id,
            )
            enqueued.append((queue, entry))
        return result

    def apply_steering(self, messages):
        applied = original_apply(self, messages)
        if applied and len(enqueued) == 1:
            queue, first = enqueued[0]
            second = queue.enqueue(
                session_id=first.session_id,
                agent_id=first.agent_id,
                text=CORRECTIONS[1],
                intent=TurnInputIntent.STEER_CURRENT,
                steer_supported=True,
                target_trace_id=first.target_trace_id,
            )
            queue.move(
                session_id=first.session_id,
                queue_id=second.queue_id,
                status_version=second.status_version,
                before_queue_id=first.queue_id,
            )
            enqueued.append((queue, second))
            applied.extend(original_apply(self, messages))
        return applied

    monkeypatch.setattr(
        _AdaptiveLoopContextAdapter, "_postprocess_outcome", postprocess
    )
    monkeypatch.setattr(
        _AdaptiveLoopContextAdapter, "apply_turn_steering", apply_steering
    )
    with _scripted_agent(
        script, tmp_path, monkeypatch, forced_tools=[], max_calls_per_run=tool_cap
    ) as (agent, calls):
        steered = agent.run(
            GOAL, session_id="closure-steering", thread_id="steered", timeout_seconds=30
        )
        control = agent.run(
            GOAL, session_id="closure-steering", thread_id="control", timeout_seconds=30
        )

    assert calls == ["A-104", "A-104"]
    assert len(script.drafts) == len(script.judges) == 2, script.kinds
    assert (
        "The tool budget or a per-tool limit has been reached."
        in _all_text(script.drafts[0])
    ) == (tool_cap == 1)
    assert [
        message["content"]
        for message in script.drafts[0]["messages"]
        if message["role"] == "user" and message["content"] in CORRECTIONS
    ] == CORRECTIONS
    assert len(enqueued) == 2
    queue, first = enqueued[0]
    completed = [
        entry
        for entry in queue.list_entries(session_id=first.session_id)
        if entry.target_trace_id == first.target_trace_id
    ]
    assert [(entry.queue_id, entry.status.value) for entry in completed] == [
        (entry.queue_id, "completed") for _, entry in reversed(enqueued)
    ]
    steered_judge, control_judge = map(_all_text, script.judges)
    assert GOAL in steered_judge
    assert GOAL in control_judge
    assert other_trace_text not in steered_judge
    assert _judge_steering(steered_judge) == [CORRECTIONS], steered.text
    assert _judge_steering(control_judge) == []
    assert "Preserve all other user constraints." in steered_judge
    assert steered.text.removeprefix("openminion: ") == EXPECTED
    assert control.text.removeprefix("openminion: ") == JUDGE_TEXT
