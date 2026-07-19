from __future__ import annotations

import tempfile

import pytest

from openminion.cli.constants import (
    OPENMINION_FOCUS_EXAMPLE_PROMPTS_ENV,
    OPENMINION_FOCUS_GREETING_ENV,
)
from openminion.cli.presentation import styles
from openminion.cli.theme import DARK
from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime
from openminion.cli.interactive.widgets.greeter import build_greeter_message
from openminion.cli.presentation.models import ChatMessage, MessageKind
from openminion.cli.interactive.widgets import FocusTranscript


@pytest.fixture(autouse=True)
def _restore_active_theme():
    original_codes = dict(styles._ANSI_CODES)
    original_name = styles.get_active_theme_name()
    styles.set_active_theme(DARK)
    yield
    styles._ANSI_CODES.clear()
    styles._ANSI_CODES.update(original_codes)
    styles._ACTIVE_THEME_NAME = original_name


def test_greeter_includes_default_content_blocks() -> None:
    runtime = _DemoFocusRuntime(working_dir="/tmp")
    msg = build_greeter_message(
        runtime=runtime,
        working_dir="/tmp/example-dir",
        theme_name="light",
    )
    assert isinstance(msg, ChatMessage)
    assert msg.kind == MessageKind.SYSTEM
    assert msg.show_header is False
    body = msg.body
    assert body.splitlines()[0] == "OpenMinion"
    assert "How can I help today?" in body
    assert "example-dir" in body
    assert "echo/demo" in body
    assert "theme: light" in body
    assert "Try:" in body
    assert "explain this codebase" in body
    assert "find all references" in body
    assert "add tests for" in body
    assert "/help" in body
    assert "@" in body
    assert "Ctrl+P" in body


def test_greeter_handles_unbound_runtime_cleanly() -> None:

    class _BareRuntime:
        agent_id = ""
        provider_name = ""
        model_name = ""

    msg = build_greeter_message(
        runtime=_BareRuntime(),
        working_dir="/tmp",
        theme_name="",
    )
    assert "(unbound)" in msg.body
    assert "(no model)" in msg.body
    assert "theme: dark" in msg.body


def test_greeter_env_override_for_examples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENMINION_FOCUS_EXAMPLE_PROMPTS_ENV, "alpha;beta;gamma")
    msg = build_greeter_message(
        runtime=_DemoFocusRuntime(working_dir="/tmp"),
        working_dir="/tmp",
        theme_name="dark",
    )
    body = msg.body
    assert "alpha" in body
    assert "beta" in body
    assert "gamma" in body
    assert "explain this codebase" not in body


def test_greeter_env_override_filters_empty_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENMINION_FOCUS_EXAMPLE_PROMPTS_ENV, "a;;b")
    msg = build_greeter_message(
        runtime=_DemoFocusRuntime(working_dir="/tmp"),
        working_dir="/tmp",
        theme_name="dark",
    )
    lines = [ln.strip() for ln in msg.body.splitlines()]
    try_idx = lines.index("Try:")
    examples = [
        lines[try_idx + 1],
        lines[try_idx + 2],
    ]
    assert "a" in examples
    assert "b" in examples
    assert lines[try_idx + 3] == ""


def test_greeter_env_override_for_greeting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENMINION_FOCUS_GREETING_ENV, "Yo, what's up?")
    msg = build_greeter_message(
        runtime=_DemoFocusRuntime(working_dir="/tmp"),
        working_dir="/tmp",
        theme_name="dark",
    )
    assert msg.body.splitlines()[0] == "Yo, what's up?"
    assert msg.body.splitlines()[1] == "How can I help today?"


@pytest.mark.asyncio
async def test_fresh_session_renders_greeter_not_welcome_banner() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="greeter-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            chat = app.screen.query_one(FocusTranscript)
            messages = list(chat._messages)
            assert messages
            first = messages[0]
            assert first.kind == MessageKind.SYSTEM
            assert first.body.startswith("OpenMinion\nHow can I help today?")
            assert "Try:" in first.body
            assert "OpenMinion CLI - single-agent interactive shell" not in first.body


@pytest.mark.asyncio
async def test_resumed_session_skips_greeter() -> None:

    class _ResumedRuntime(_DemoFocusRuntime):
        def get_current_history(self):  # type: ignore[override]
            return [
                ChatMessage(
                    kind=MessageKind.USER,
                    sender="you",
                    body="prior message",
                ),
            ]

    with tempfile.TemporaryDirectory() as tmp:
        app = FocusApp(
            runtime=_ResumedRuntime(working_dir=tmp),
            working_dir=tmp,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            chat = app.screen.query_one(FocusTranscript)
            bodies = [m.body for m in chat._messages]
            assert any("prior message" in b for b in bodies)
            assert not any("How can I help today?" in b for b in bodies)


def test_fcpp_welcome_builder_still_importable() -> None:
    from openminion.cli.interactive.widgets.welcome import build_welcome_message

    assert callable(build_welcome_message)
