from __future__ import annotations

import asyncio
import io
from typing import Iterator

import pytest

from openminion.cli.chat.approval import (
    ChatApprovalState,
    build_chat_approval_callback,
)


def _scripted_input(answers: list[str]):
    iterator: Iterator[str] = iter(answers)

    def _input(prompt: str) -> str:
        del prompt
        return next(iterator)

    return _input


def _run(coro):
    return asyncio.run(coro)


def test_allow_once_returns_true_and_does_not_persist() -> None:
    state = ChatApprovalState()
    out = io.StringIO()
    cb = build_chat_approval_callback(
        state=state,
        input_fn=_scripted_input(["a", "d"]),
        output_stream=out,
    )
    assert _run(cb("exec.run", {"command": "ls"}, None)) is True
    assert _run(cb("exec.run", {"command": "ls"}, None)) is False
    assert "exec.run" not in state.session_grants


def test_allow_session_remembers_for_subsequent_calls() -> None:
    state = ChatApprovalState()
    out = io.StringIO()
    cb = build_chat_approval_callback(
        state=state,
        input_fn=_scripted_input(["s"]),  # second call skips prompt
        output_stream=out,
    )
    assert _run(cb("exec.run", {"command": "ls"}, None)) is True
    assert "exec.run" in state.session_grants
    assert _run(cb("exec.run", {"command": "pwd"}, None)) is True


def test_deny_returns_false_and_does_not_persist() -> None:
    state = ChatApprovalState()
    out = io.StringIO()
    cb = build_chat_approval_callback(
        state=state,
        input_fn=_scripted_input(["d"]),
        output_stream=out,
    )
    assert _run(cb("exec.run", {"command": "rm -rf /"}, None)) is False
    assert state.session_grants == set()


@pytest.mark.parametrize(
    "raw_choice",
    ["maybe", "always", "3"],
)
def test_non_granting_inputs_fail_closed(raw_choice: str) -> None:
    state = ChatApprovalState()
    out = io.StringIO()
    cb = build_chat_approval_callback(
        state=state,
        input_fn=_scripted_input([raw_choice]),
        output_stream=out,
    )
    assert _run(cb("exec.run", {"command": "ls"}, None)) is False
    assert "exec.run" not in state.session_grants
    if raw_choice == "always":
        assert (
            "persistent approval grants are not yet implemented" not in out.getvalue()
        )


def test_pre_granted_tool_skips_prompt() -> None:
    state = ChatApprovalState(session_grants={"exec.run"})
    out = io.StringIO()
    cb = build_chat_approval_callback(
        state=state,
        input_fn=_scripted_input([]),
        output_stream=out,
    )
    assert _run(cb("exec.run", {"command": "ls"}, None)) is True
    assert out.getvalue() == ""


def test_prompt_text_includes_tool_call_line_and_choices() -> None:
    state = ChatApprovalState()
    out = io.StringIO()
    cb = build_chat_approval_callback(
        state=state,
        input_fn=_scripted_input(["d"]),
        output_stream=out,
    )
    _run(cb("exec.run", {"command": "ls -la /tmp"}, None))
    output = out.getvalue()
    assert "Approval required:" in output
    assert "exec.run(" in output
    assert "[a] allow once" in output
    assert "[s] allow this session" in output
    assert "[d] deny" in output


def test_callback_signature_is_awaitable_bool() -> None:
    state = ChatApprovalState()
    cb = build_chat_approval_callback(
        state=state,
        input_fn=_scripted_input(["a"]),
        output_stream=io.StringIO(),
    )
    result = cb("exec.run", {"command": "ls"}, None)
    assert asyncio.iscoroutine(result)
    assert _run(result) is True


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a", True),
        ("A", True),
        ("allow", True),
        ("y", True),
        ("yes", True),
        ("1", True),
        ("s", True),
        ("session", True),
        ("2", True),
        ("d", False),
        ("deny", False),
        ("n", False),
        ("no", False),
        ("", False),
        ("   ", False),
        ("xyz", False),
    ],
)
def test_choice_resolution_is_robust(raw: str, expected: bool) -> None:
    state = ChatApprovalState()
    cb = build_chat_approval_callback(
        state=state,
        input_fn=_scripted_input([raw]),
        output_stream=io.StringIO(),
    )
    assert _run(cb("exec.run", {"command": "ls"}, None)) is expected
