from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.loop.tools import AdaptiveToolLoopState
from openminion.modules.brain.loop.tools.postprocess.evidence_closeout import (
    mutating_file_evidence_fallback_text,
    tool_evidence_closeout_text,
)
from openminion.modules.brain.loop.tools.postprocess.loop import (
    _mutating_file_closeout_message,
    _record_mutating_file_repetition,
)
from openminion.modules.brain.schemas import ActionResult
from openminion.modules.llm.schemas import Message


def _tool_call(name: str, path: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, arguments={"path": path})


def _success(path: str) -> SimpleNamespace:
    return SimpleNamespace(
        action_result=ActionResult(
            command_id="cmd-1",
            status="success",
            summary="wrote file",
            outputs={"path": path},
        )
    )


def test_repeated_successful_file_mutation_requests_answer_only_closeout() -> None:
    state = AdaptiveToolLoopState()
    batch = [(_tool_call("file.write", "module.py"), _success("module.py"))]

    assert _record_mutating_file_repetition(state, batch) is False
    assert _record_mutating_file_repetition(state, batch) is False
    assert _record_mutating_file_repetition(state, batch) is True

    assert state.scratchpad["mutating_file_answer_only_closure_pending"] is True
    assert state.scratchpad["mutating_file_success_path_counts"]["module.py"] == 3
    message = _mutating_file_closeout_message(state)
    assert message.role == "system"
    assert "Stop calling file mutation tools" in message.content


def test_mutating_file_repetition_ignores_non_mutating_tools() -> None:
    state = AdaptiveToolLoopState()
    batch = [(_tool_call("file.read", "module.py"), _success("module.py"))]

    assert _record_mutating_file_repetition(state, batch) is False

    assert "mutating_file_answer_only_closure_pending" not in state.scratchpad


def test_mutating_file_fallback_preserves_requested_result_marker() -> None:
    state = AdaptiveToolLoopState(
        messages=[
            Message(
                role="user",
                content=(
                    "Use the exact label `result:` and finish with files changed "
                    "plus validation result."
                ),
            )
        ],
        scratchpad={
            "adaptive.tool_results": [
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "content": "wrote module.py",
                    "data": {"path": "module.py"},
                }
            ]
        },
    )

    text = mutating_file_evidence_fallback_text(state)

    assert "result:" in text
    assert "files changed: module.py" in text
    assert "validation:" in text


def test_tool_evidence_closeout_preserves_research_labels() -> None:
    state = AdaptiveToolLoopState(
        messages=[
            Message(
                role="user",
                content="Finish with exact labels `tradeoffs:` and `recommendation:`.",
            )
        ],
        scratchpad={
            "adaptive.tool_results": [
                {
                    "tool_name": "web.search",
                    "ok": True,
                    "content": "found terminal UX evidence",
                    "data": {},
                }
            ]
        },
    )

    text = tool_evidence_closeout_text(state, reason="tool budget exhausted.")

    assert "tradeoffs:" in text
    assert "recommendation:" in text
    assert "tool evidence:" in text
