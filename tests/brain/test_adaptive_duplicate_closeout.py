from __future__ import annotations

from openminion.modules.brain.loop.adaptive.finalization import (
    _duplicate_exhaustion_evidence_outcome,
)
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    ADAPTIVE_TERM_FINAL_TEXT,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopState,
)
from openminion.modules.llm.schemas import Message


def test_duplicate_exhaustion_closes_from_tool_evidence_with_requested_markers() -> None:
    state = AdaptiveToolLoopState(
        messages=[
            Message(
                role="user",
                content=(
                    "Research the topic and finish with the exact labels "
                    "`tradeoffs:` and `recommendation:`."
                ),
            )
        ],
        scratchpad={
            "adaptive.tool_results": [
                {
                    "tool_name": "web.search",
                    "ok": True,
                    "content": "three source summaries about terminal-agent UX",
                    "data": {"source": "tavily"},
                }
            ]
        },
    )
    outcome = AdaptiveToolLoopOutcome(
        profile_name="general",
        mode_name="act",
        termination_reason=ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        state=state,
        allowed_tools=frozenset({"web.search"}),
        error_message="repeated identical tool calls",
    )

    closed = _duplicate_exhaustion_evidence_outcome(outcome)

    assert closed is not None
    assert closed.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert closed.error_message is None
    assert closed.finalization_status == {
        "status": "final_answer",
        "reasoning": "successful tool evidence fallback after repeated tool calls",
    }
    assert "tradeoffs:" in str(closed.final_text).lower()
    assert "recommendation:" in str(closed.final_text).lower()
    assert "tool evidence:" in str(closed.final_text).lower()
    assert state.scratchpad["adaptive.duplicate_exhaustion_used_evidence_closeout"] is True


def test_duplicate_exhaustion_does_not_close_file_mutation_request_from_readonly_evidence() -> None:
    state = AdaptiveToolLoopState(
        messages=[
            Message(
                role="user",
                content=(
                    "In the current directory, implement it with file.write/file.read "
                    "and finish with `result:`."
                ),
            )
        ],
        scratchpad={
            "adaptive.tool_results": [
                {
                    "tool_name": "file.list_dir",
                    "ok": True,
                    "content": "",
                    "data": {"path": ".", "count": 0},
                }
            ]
        },
    )
    outcome = AdaptiveToolLoopOutcome(
        profile_name="general",
        mode_name="act",
        termination_reason=ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        state=state,
        allowed_tools=frozenset({"file.list_dir", "file.write"}),
        error_message="repeated identical tool calls",
    )

    assert _duplicate_exhaustion_evidence_outcome(outcome) is None
