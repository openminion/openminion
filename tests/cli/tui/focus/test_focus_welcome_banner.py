from __future__ import annotations

import tempfile

import pytest

from openminion import __version__
from openminion.cli.presentation import styles
from openminion.cli.theme import DARK
from openminion.cli.tui.focus.app import FocusApp, _DemoFocusRuntime
from openminion.cli.tui.focus.widgets.welcome import build_welcome_message
from openminion.cli.tui.presentation.models import ChatMessage, MessageKind
from openminion.cli.tui.focus.widgets import FocusTranscript


@pytest.fixture(autouse=True)
def _restore_active_theme():
    original_codes = dict(styles._ANSI_CODES)
    original_name = styles.get_active_theme_name()
    styles.set_active_theme(DARK)
    yield
    styles._ANSI_CODES.clear()
    styles._ANSI_CODES.update(original_codes)
    styles._ACTIVE_THEME_NAME = original_name


def test_build_welcome_message_includes_required_fields() -> None:
    runtime = _DemoFocusRuntime(working_dir="/tmp")
    msg = build_welcome_message(
        runtime=runtime,
        working_dir="/tmp/example-dir",
        theme_name="light",
    )
    assert isinstance(msg, ChatMessage)
    assert msg.kind == MessageKind.SYSTEM
    assert msg.show_header is False
    body = msg.body
    assert "OpenMinion focus" in body
    assert __version__ in body
    assert "example-dir" in body
    assert "agent:" in body
    assert "model:" in body
    assert "theme: light" in body
    assert "/help" in body
    assert "@<path>" in body
    assert "Ctrl+P" in body


def test_build_welcome_message_handles_missing_runtime_fields() -> None:

    class _BareRuntime:
        agent_id = ""
        provider_name = ""
        model_name = ""

    msg = build_welcome_message(
        runtime=_BareRuntime(),
        working_dir="/tmp",
        theme_name="",
    )
    assert "(unbound)" in msg.body
    assert "(no model)" in msg.body
    assert "theme: dark" in msg.body


@pytest.mark.asyncio
async def test_fresh_session_uses_fiu_greeter_mount_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="fresh-mount-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            chat = app.screen.query_one(FocusTranscript)
            messages = list(chat._messages)
            assert messages, "fresh session should render at least one message"
            first = messages[0]
            assert first.kind == MessageKind.SYSTEM
            assert "How can I help today?" in first.body
            assert "Try:" in first.body
            assert "OpenMinion focus — single-agent shell" not in first.body
            assert callable(build_welcome_message)


@pytest.mark.asyncio
async def test_resumed_session_skips_welcome_banner() -> None:

    class _ResumedRuntime(_DemoFocusRuntime):
        def get_current_history(self):  # type: ignore[override]
            return [
                ChatMessage(
                    kind=MessageKind.USER,
                    sender="you",
                    body="prior message",
                ),
                ChatMessage(
                    kind=MessageKind.AGENT,
                    sender="agent",
                    body="prior reply",
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
            assert not any("OpenMinion focus" in b for b in bodies), (
                "resumed session must not double-render the welcome banner"
            )
