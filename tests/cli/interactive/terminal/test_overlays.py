from __future__ import annotations

import io
from types import SimpleNamespace

from rich.console import Console

from openminion.cli.interactive.terminal.overlays import TerminalOverlayPresenter
from openminion.cli.presentation.contracts import OverlayPresenter


class _StubSession:
    def __init__(self, replies: list[str | Exception]) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    async def prompt_async(self, *args, **kwargs):
        self.prompts.append(str(args[0]) if args else "")
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
    console, output = _make_console()
    session = _StubSession(["y"])
    overlay = TerminalOverlayPresenter(console=console, prompt_session=session)
    assert overlay.present_approval("Run dangerous command?") == "allow"
    assert output.getvalue() == "Run dangerous command?\n"
    assert session.prompts == ["[y]es / [N]o / [a]lways: "]


def test_approval_prints_full_long_command_outside_input_prompt() -> None:
    console, output = _make_console()
    session = _StubSession(["n"])
    overlay = TerminalOverlayPresenter(console=console, prompt_session=session)
    command = (
        'Approval required: exec.run("ssh -o BatchMode=yes '
        '-o ConnectTimeout=3 -o StrictHostKeyChecking=yes localhost true")'
    )

    assert overlay.present_approval(command) == "deny"

    rendered = output.getvalue()
    assert "BatchMode=yes" in rendered
    assert "ConnectTimeout=3" in rendered
    assert "StrictHostKeyChecking=yes" in rendered
    assert 'localhost true")' in rendered
    assert "…" not in rendered
    assert session.prompts == ["[y]es / [N]o / [a]lways: "]


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
