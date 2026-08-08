from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.loop.tools import AdaptiveToolLoopState
from openminion.modules.brain.loop.tools.no_tool import (
    _tool_attempt_evidence_closeout_text,
)
from openminion.modules.brain.loop.tools.postprocess.evidence_closeout import (
    mutating_file_evidence_can_closeout,
    mutating_file_evidence_fallback_text,
    requested_validation_without_exec_run,
    tool_evidence_closeout_text,
)
from openminion.modules.brain.loop.tools.postprocess.loop import (
    _mutating_file_closeout_message,
    _record_mutating_file_repetition,
    _track_successful_mutating_file_progress,
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


def test_repeated_file_mutation_with_missing_test_redirects_to_artifact_write() -> None:
    state = AdaptiveToolLoopState(
        messages=[
            Message(
                role="user",
                content=(
                    "Create a tiny Python module and test. Use file.write for files."
                ),
            )
        ],
        scratchpad={
            "adaptive.tool_results": [
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "data": {"path": "module.py"},
                }
            ]
        },
    )
    batch = [(_tool_call("file.write", "module.py"), _success("module.py"))]
    iteration_tool_sequences: list[tuple[str, ...]] = []

    _track_successful_mutating_file_progress(
        state,
        batch,
        iteration_tool_sequences=iteration_tool_sequences,
    )
    _track_successful_mutating_file_progress(
        state,
        batch,
        iteration_tool_sequences=iteration_tool_sequences,
    )
    _track_successful_mutating_file_progress(
        state,
        batch,
        iteration_tool_sequences=iteration_tool_sequences,
    )

    assert "mutating_file_answer_only_closure_pending" not in state.scratchpad
    assert state.scratchpad["mutating_file_repetition_missing_artifacts"] == [
        "test file"
    ]
    assert "Stop rewriting the same file" in state.messages[-1].content


def test_missing_requested_file_artifacts_include_cli_entry() -> None:
    state = AdaptiveToolLoopState(
        messages=[
            Message(
                role="user",
                content=(
                    "Build a tiny Python CLI project with a module, CLI entry, "
                    "tests, and README. Use file.write for files."
                ),
            )
        ],
        scratchpad={
            "adaptive.tool_results": [
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "data": {"path": "greet.py"},
                }
            ]
        },
    )

    from openminion.modules.brain.loop.tools.postprocess.evidence_closeout import (
        missing_requested_file_artifact_labels,
    )

    assert missing_requested_file_artifact_labels(state) == (
        "README",
        "CLI entry",
        "test file",
    )


def test_missing_requested_file_artifacts_include_named_module() -> None:
    state = AdaptiveToolLoopState(
        messages=[
            Message(
                role="user",
                content=(
                    "Implement module code in `section_summary.py`, plus CLI "
                    "entry, tests, and README."
                ),
            )
        ],
        scratchpad={
            "adaptive.tool_results": [
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "data": {"path": "README.md"},
                },
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "data": {"path": "cli.py"},
                },
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "data": {"path": "test_module.py"},
                },
            ]
        },
    )

    from openminion.modules.brain.loop.tools.postprocess.evidence_closeout import (
        missing_requested_file_artifact_labels,
    )

    assert missing_requested_file_artifact_labels(state) == ("section_summary.py",)


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
                },
                {
                    "tool_name": "exec.run",
                    "ok": True,
                    "content": "1 passed",
                    "data": {"stdout": "1 passed"},
                },
            ]
        },
    )

    text = mutating_file_evidence_fallback_text(state)

    assert "result:" in text
    assert "files changed: module.py" in text
    assert "validation:" in text


def test_exact_label_without_backticks_is_preserved() -> None:
    state = AdaptiveToolLoopState(
        messages=[
            Message(
                role="user",
                content="Finish with exact label result: and list changed files.",
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


def test_validation_request_returns_truthful_file_write_fallback() -> None:
    state = AdaptiveToolLoopState(
        messages=[
            Message(
                role="user",
                content=(
                    "Use file.write for files, run exactly pytest with exec.run, "
                    "and finish with exact label result: plus validation result."
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

    assert requested_validation_without_exec_run(state) is True
    mutating_text = mutating_file_evidence_fallback_text(state)
    tool_text = tool_evidence_closeout_text(state, reason="tool budget exhausted.")
    attempt_text = _tool_attempt_evidence_closeout_text(
        state,
        reason="tool budget exhausted.",
    )

    assert "result:" in mutating_text
    assert "files changed: module.py" in mutating_text
    assert "validation: deterministic validation was not captured" in mutating_text
    assert "result:" in tool_text
    assert "validation: deterministic validation was not captured" in tool_text
    assert "result:" in attempt_text
    assert "validation: no successful validation evidence was captured" in attempt_text


def test_validation_request_allows_fallback_with_exec_run_evidence() -> None:
    state = AdaptiveToolLoopState(
        messages=[
            Message(
                role="user",
                content=(
                    "Use file.write for files, run exactly pytest with exec.run, "
                    "and finish with exact label result: plus validation result."
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
                },
                {
                    "tool_name": "exec.run",
                    "ok": True,
                    "content": "1 passed",
                    "data": {"stdout": "1 passed"},
                },
            ]
        },
    )

    text = mutating_file_evidence_fallback_text(state)

    assert requested_validation_without_exec_run(state) is False
    assert "result:" in text
    assert "validation:" in text


def test_validation_request_without_exec_run_blocks_mutating_file_closeout() -> None:
    state = AdaptiveToolLoopState(
        messages=[
            Message(
                role="user",
                content=(
                    "Use file.write for files, run a focused check with exec.run, "
                    "and finish with the exact label `result:`."
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

    assert requested_validation_without_exec_run(state) is True
    assert mutating_file_evidence_can_closeout(state) is False


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


def test_tool_evidence_closeout_preserves_prose_next_steps_request() -> None:
    state = AdaptiveToolLoopState(
        messages=[
            Message(
                role="user",
                content="Write a research synthesis and end with prioritized next steps.",
            )
        ],
        scratchpad={
            "adaptive.tool_results": [
                {
                    "tool_name": "web.search",
                    "ok": True,
                    "content": "found CLI harness evidence",
                    "data": {},
                }
            ]
        },
    )

    text = tool_evidence_closeout_text(state, reason="tool budget exhausted.")

    assert "next steps:" in text
    assert "tool evidence:" in text
