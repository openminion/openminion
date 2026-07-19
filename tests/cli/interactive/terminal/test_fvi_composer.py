from __future__ import annotations

import asyncio
import inspect

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from openminion.cli.interactive.terminal.composer import (
    TerminalComposer,
    _SlashAndAtCompleter,
)


# ── Composer surface preserved ───────────────────────────────────


def test_composer_accepts_slash_commands_kwarg() -> None:
    sig = inspect.signature(TerminalComposer.__init__)
    assert "slash_commands" in sig.parameters


def test_composer_accepts_bottom_toolbar_kwarg() -> None:
    sig = inspect.signature(TerminalComposer.__init__)
    assert "bottom_toolbar" in sig.parameters


def test_composer_accepts_on_ctrl_l_kwarg() -> None:
    sig = inspect.signature(TerminalComposer.__init__)
    assert "on_ctrl_l" in sig.parameters
    assert sig.parameters["on_ctrl_l"].default is None


def test_composer_accepts_on_ctrl_o_kwarg() -> None:
    sig = inspect.signature(TerminalComposer.__init__)
    assert "on_ctrl_o" in sig.parameters
    assert sig.parameters["on_ctrl_o"].default is None


def test_composer_constructs_without_callbacks() -> None:
    composer = TerminalComposer(slash_commands=("/help",))
    assert composer is not None


def test_composer_constructs_with_callbacks() -> None:
    composer = TerminalComposer(
        slash_commands=("/help",),
        on_ctrl_l=lambda: None,
        on_ctrl_o=lambda: None,
    )
    assert composer is not None


# ── Slash + @ completer preserved ────────────────────────────────


def test_slash_at_completer_present() -> None:
    composer = TerminalComposer(slash_commands=("/help", "/exit"))
    assert isinstance(composer._completer, _SlashAndAtCompleter)


# ── read_line passes placeholder to prompt_async ────────────────


def test_read_line_passes_placeholder_kwarg() -> None:
    src = inspect.getsource(TerminalComposer.read_line)
    assert "placeholder=" in src
    assert "placeholder=self._formatted_placeholder" in src


def test_read_line_still_passes_patch_stdout_context() -> None:
    src = inspect.getsource(TerminalComposer.read_line)
    assert "patch_stdout(raw=True)" in src


def test_read_line_still_passes_completer() -> None:
    src = inspect.getsource(TerminalComposer.read_line)
    assert "completer=self._completer" in src


def test_read_line_still_passes_bottom_toolbar() -> None:
    src = inspect.getsource(TerminalComposer.read_line)
    assert "bottom_toolbar=self._formatted_bottom_toolbar" in src


def test_read_line_enables_completion_while_typing() -> None:
    src = inspect.getsource(TerminalComposer.read_line)
    assert "complete_while_typing=True" in src


def test_read_line_still_passes_multiline() -> None:
    src = inspect.getsource(TerminalComposer.read_line)
    assert "multiline=Condition(lambda: self._multiline)" in src


# ── End-to-end via pipe_input ────────────────────────────────────


def test_placeholder_renders_when_input_empty() -> None:
    from io import StringIO
    from contextlib import redirect_stdout

    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import FormattedText

    captured = StringIO()
    placeholder_str = "Ask anything · @ to mention a file · / for commands"

    async def _run() -> str:
        with create_pipe_input() as pipe:
            session = PromptSession(input=pipe, output=DummyOutput())
            pipe.send_text("typed\n")
            return await session.prompt_async(
                "> ",
                placeholder=FormattedText([("class:placeholder", placeholder_str)]),
            )

    with redirect_stdout(captured):
        result = asyncio.run(_run())

    assert result == "typed"


def test_read_line_disabled_raises() -> None:
    composer = TerminalComposer(slash_commands=("/help",))
    composer.set_disabled(True)

    async def _run() -> str:
        return await composer.read_line()

    import pytest

    with pytest.raises(RuntimeError, match="composer disabled"):
        asyncio.run(_run())
