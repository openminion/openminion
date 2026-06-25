from __future__ import annotations

import io
from types import SimpleNamespace

from rich.console import Console

from openminion.cli.tui.terminal.overlays import TerminalOverlayPresenter
from openminion.cli.tui.presentation.contracts import OverlayPresenter


class _StubSession:
    def __init__(self, replies: list[str | Exception]) -> None:
        self._replies = list(replies)

    async def prompt_async(self, *args, **kwargs):
        if not self._replies:
            raise EOFError()
        next_reply = self._replies.pop(0)
        if isinstance(next_reply, Exception):
            raise next_reply
        return next_reply


def _make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, width=80), buf


def test_overlay_satisfies_protocol() -> None:
    console, _ = _make_console()
    overlay = TerminalOverlayPresenter(console=console, prompt_session=_StubSession([]))
    assert isinstance(overlay, OverlayPresenter)


def test_resume_picker_returns_selected_session_id() -> None:
    console, _ = _make_console()
    overlay = TerminalOverlayPresenter(
        console=console, prompt_session=_StubSession(["2"])
    )
    sessions = [
        SimpleNamespace(id="s1", label="first"),
        SimpleNamespace(id="s2", label="second"),
        SimpleNamespace(id="s3", label="third"),
    ]
    result = overlay.present_resume_picker(sessions)
    assert result == "s2"


def test_resume_picker_empty_input_returns_none() -> None:
    console, _ = _make_console()
    overlay = TerminalOverlayPresenter(
        console=console, prompt_session=_StubSession([""])
    )
    sessions = [SimpleNamespace(id="s1")]
    assert overlay.present_resume_picker(sessions) is None


def test_resume_picker_eof_returns_none() -> None:
    console, _ = _make_console()
    overlay = TerminalOverlayPresenter(
        console=console, prompt_session=_StubSession([EOFError()])
    )
    sessions = [SimpleNamespace(id="s1")]
    assert overlay.present_resume_picker(sessions) is None


def test_resume_picker_no_sessions_returns_none() -> None:
    console, _ = _make_console()
    overlay = TerminalOverlayPresenter(console=console, prompt_session=_StubSession([]))
    assert overlay.present_resume_picker([]) is None


def test_approval_yes_returns_allow() -> None:
    console, _ = _make_console()
    overlay = TerminalOverlayPresenter(
        console=console, prompt_session=_StubSession(["y"])
    )
    assert overlay.present_approval("Run dangerous command?") == "allow"


def test_approval_always_returns_always() -> None:
    console, _ = _make_console()
    overlay = TerminalOverlayPresenter(
        console=console, prompt_session=_StubSession(["a"])
    )
    assert overlay.present_approval("Run cmd?") == "always"


def test_approval_no_or_empty_returns_deny() -> None:
    console, _ = _make_console()
    overlay = TerminalOverlayPresenter(
        console=console, prompt_session=_StubSession([""])
    )
    assert overlay.present_approval("Run cmd?") == "deny"


def test_completion_returns_user_reply() -> None:
    console, _ = _make_console()
    overlay = TerminalOverlayPresenter(
        console=console, prompt_session=_StubSession(["my answer"])
    )
    assert overlay.present_completion("Confirm?") == "my answer"


def test_confirm_yes_returns_true() -> None:
    console, _ = _make_console()
    overlay = TerminalOverlayPresenter(
        console=console,
        prompt_session=_StubSession(["y"]),
    )
    assert overlay.present_confirm("Exit focus mode?") is True


def test_confirm_empty_uses_default() -> None:
    console, _ = _make_console()
    overlay = TerminalOverlayPresenter(
        console=console,
        prompt_session=_StubSession([""]),
    )
    assert overlay.present_confirm("Exit focus mode?", default=True) is True


def test_confirm_keyboard_interrupt_returns_false() -> None:
    console, _ = _make_console()
    overlay = TerminalOverlayPresenter(
        console=console,
        prompt_session=_StubSession([KeyboardInterrupt()]),
    )
    assert overlay.present_confirm("Exit focus mode?") is False
