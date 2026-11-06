"""Repeated-tool closeouts carry current-turn steering into tool-free requests."""

import pytest

from openminion.modules.brain.loop.tools.budget_control import (
    _force_circular_pattern_answer_only_finalization,
)
from tests.brain.tool_loops.test_budget_steering import (
    CORRECTION,
    ORIGINAL,
    _answer,
    _assert_applied_once,
    _FakeRuntime,
    _profile,
    _read,
    _run,
    _SteeringContext,
)


@pytest.mark.parametrize("arrival", ["none", "applied", "pending", "retry"])
def test_circular_closeout_preserves_steering(arrival):
    ctx = _SteeringContext(
        before=(CORRECTION,) if arrival in {"applied", "retry"} else ()
    )
    loop = _run(ctx, _FakeRuntime(responses=[_read(), _answer("done")]), cap=4)
    corrections = () if arrival == "none" else (CORRECTION,)
    newer_correction = "Show totals in EUR."
    if arrival == "retry":
        corrections += (newer_correction,)

    def enqueue(text):
        ctx.applied_ids.append(ctx.enqueue(text).queue_id)

    if arrival == "pending":
        enqueue(CORRECTION)

    class CloseoutRuntime(_FakeRuntime):
        def complete(self, **kwargs):
            response = super().complete(**kwargs)
            if arrival == "retry" and len(self.calls) == 1:
                enqueue(newer_correction)
                return response.model_copy(update={"empty_payload_recovered": True})
            return response

    runtime = CloseoutRuntime(responses=[_answer("done"), _answer("done")])
    allowed = frozenset({"file.read"})
    outcome = _force_circular_pattern_answer_only_finalization(
        loop_ctx=ctx,
        profile=_profile(profile_name="general_adaptive_v1", allowed_tools=allowed),
        loop_state=loop.state,
        runtime=runtime,
        model="probe",
        max_output_tokens=None,
        metadata=None,
        allowed_tools=allowed,
        public_mode_tag="test",
    )

    assert outcome is not None
    assert outcome.final_text == "done"
    assert outcome.allowed_tools == allowed
    assert len(ctx.commands) == 1
    assert len(runtime.calls) == (2 if arrival == "retry" else 1)
    for call in runtime.calls:
        assert call["tools"] == []
        assert call["tool_choice"] == "none"
        assert any(ORIGINAL in str(m.content) for m in call["messages"])
        assert any("known_eu_subtotal=175" in str(m.content) for m in call["messages"])
    if arrival == "retry":
        assert [
            msg.content
            for msg in runtime.calls[0]["messages"]
            if msg.meta.get("turn_input_queue_id")
        ] == [CORRECTION]
    _assert_applied_once(ctx, runtime.calls[-1]["messages"], corrections)


@pytest.mark.parametrize("arrival", ["none", "applied", "pending", "compact"])
def test_duplicate_closeout_preserves_steering(arrival):
    ctx = _SteeringContext(before=(CORRECTION,) if arrival == "applied" else ())
    corrections = () if arrival == "none" else (CORRECTION,)

    class DuplicateRuntime(_FakeRuntime):
        def complete(self, **kwargs):
            response = super().complete(**kwargs)
            if (arrival, len(self.calls)) in {("pending", 2), ("compact", 4)}:
                ctx.applied_ids.append(ctx.enqueue(CORRECTION).queue_id)
            if arrival == "compact" and len(self.calls) == 3:
                return response.model_copy(update={"empty_payload_recovered": True})
            return response

    responses = [_read(), _read("duplicate"), _answer("done")]
    if arrival == "compact":
        responses.extend([_read("suppressed"), _answer("done")])
    runtime = DuplicateRuntime(responses=responses)

    outcome = _run(ctx, runtime, cap=4)

    assert outcome.final_text == "done"
    assert outcome.allowed_tools == frozenset({"file.read", "plan"})
    assert len(ctx.commands) == 1
    assert outcome.state.scratchpad["duplicate_batch_answer_only_closure_forced"]
    if arrival == "compact":
        assert outcome.state.scratchpad[
            "tool_choice_none_compact_answer_only_retry_used"
        ]
        assert len(runtime.calls) == 5
    else:
        assert len(runtime.calls) == 3
    closeout = runtime.calls[-1]
    assert closeout["tools"] == []
    assert closeout["tool_choice"] == "none"
    assert any(ORIGINAL in str(m.content) for m in closeout["messages"])
    assert any("known_eu_subtotal=175" in str(m.content) for m in closeout["messages"])
    _assert_applied_once(ctx, closeout["messages"], corrections)
