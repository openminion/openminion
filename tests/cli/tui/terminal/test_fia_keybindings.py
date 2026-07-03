from __future__ import annotations

import io
import inspect
import subprocess
from typing import Any
from unittest.mock import patch

from rich.console import Console

from openminion.cli.tui.terminal.composer import TerminalComposer
from openminion.cli.tui.terminal.shell import _copy_to_clipboard
from openminion.cli.tui.terminal.transcript import TerminalTranscript


def _make_console_and_transcript() -> tuple[Console, TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    return console, TerminalTranscript(console), buf


def test_composer_accepts_on_ctrl_l() -> None:
    sig = inspect.signature(TerminalComposer.__init__)
    assert "on_ctrl_l" in sig.parameters
    assert sig.parameters["on_ctrl_l"].default is None


def test_composer_accepts_on_ctrl_o() -> None:
    sig = inspect.signature(TerminalComposer.__init__)
    assert "on_ctrl_o" in sig.parameters
    assert sig.parameters["on_ctrl_o"].default is None


def test_composer_no_kwargs_still_works() -> None:
    composer = TerminalComposer(slash_commands=("/help",))
    assert composer is not None


def test_composer_with_callbacks_works() -> None:
    fired: dict[str, int] = {"l": 0, "o": 0}

    def _l() -> None:
        fired["l"] += 1

    def _o() -> None:
        fired["o"] += 1

    composer = TerminalComposer(
        slash_commands=("/help",),
        on_ctrl_l=_l,
        on_ctrl_o=_o,
    )
    assert composer is not None
    assert fired == {"l": 0, "o": 0}


def test_copy_to_clipboard_uses_pbcopy_when_available() -> None:
    class _OK:
        returncode = 0

    calls: list[tuple[str, ...]] = []

    def _fake_run(cmd: tuple[str, ...], **kwargs: Any) -> Any:
        calls.append(tuple(cmd))
        if cmd[0] == "pbcopy":
            return _OK()
        raise FileNotFoundError(cmd[0])

    with patch.object(subprocess, "run", _fake_run):
        ok = _copy_to_clipboard("hello world")
    assert ok is True
    assert calls[0][0] == "pbcopy"


def test_copy_to_clipboard_falls_through_to_xclip() -> None:
    class _OK:
        returncode = 0

    def _fake_run(cmd: tuple[str, ...], **kwargs: Any) -> Any:
        if cmd[0] == "pbcopy":
            raise FileNotFoundError("pbcopy")
        if cmd[0] == "wl-copy":
            raise FileNotFoundError("wl-copy")
        if cmd[0] == "xclip":
            return _OK()
        raise FileNotFoundError(cmd[0])

    with patch.object(subprocess, "run", _fake_run):
        ok = _copy_to_clipboard("hello world")
    assert ok is True


def test_copy_to_clipboard_returns_false_when_no_tool_available() -> None:
    def _fake_run(cmd: tuple[str, ...], **kwargs: Any) -> Any:
        raise FileNotFoundError(cmd[0])

    with patch.object(subprocess, "run", _fake_run):
        ok = _copy_to_clipboard("hello")
    assert ok is False


def test_copy_to_clipboard_returns_false_when_tool_fails() -> None:
    class _Fail:
        returncode = 1

    def _fake_run(cmd: tuple[str, ...], **kwargs: Any) -> Any:
        return _Fail()

    with patch.object(subprocess, "run", _fake_run):
        ok = _copy_to_clipboard("hello")
    assert ok is False


def test_copy_to_clipboard_handles_arbitrary_exception() -> None:
    def _fake_run(cmd: tuple[str, ...], **kwargs: Any) -> Any:
        raise RuntimeError("unexpected")

    with patch.object(subprocess, "run", _fake_run):
        ok = _copy_to_clipboard("hello")
    assert ok is False


def test_copy_to_clipboard_passes_payload_as_utf8() -> None:
    captured: dict[str, Any] = {}

    class _OK:
        returncode = 0

    def _fake_run(cmd: tuple[str, ...], **kwargs: Any) -> Any:
        if cmd[0] == "pbcopy":
            captured["input"] = kwargs.get("input")
            return _OK()
        raise FileNotFoundError(cmd[0])

    with patch.object(subprocess, "run", _fake_run):
        _copy_to_clipboard("héllo — 中文")
    assert isinstance(captured["input"], bytes)
    assert "héllo".encode("utf-8") in captured["input"]


def test_no_pyperclip_import() -> None:
    from openminion.cli.tui.terminal import shell

    src = inspect.getsource(shell)
    assert "pyperclip" not in src
