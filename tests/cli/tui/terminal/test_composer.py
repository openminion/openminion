from __future__ import annotations

from pathlib import Path

import pytest
from prompt_toolkit.formatted_text.ansi import ANSI
from prompt_toolkit.history import FileHistory

from openminion.cli.tui.terminal.composer import TerminalComposer
from openminion.cli.tui.presentation.contracts import Composer


def test_composer_satisfies_protocol() -> None:
    c = TerminalComposer()
    assert isinstance(c, Composer)


def test_set_resumed_flips_prompt_prefix() -> None:
    c = TerminalComposer()
    assert c._prompt_text() == "❯ "
    c.set_resumed(True)
    assert c._prompt_text() == "↳ "
    c.set_resumed(False)
    assert c._prompt_text() == "❯ "


def test_set_disabled_changes_prompt_and_blocks_read() -> None:
    c = TerminalComposer()
    c.set_disabled(True)
    assert c._prompt_text() == "… "

    import asyncio

    async def _try_read() -> None:
        await c.read_line()

    with pytest.raises(RuntimeError):
        asyncio.run(_try_read())


def test_toggle_multiline_flips_state() -> None:
    c = TerminalComposer()
    assert c._multiline is False
    c.toggle_multiline()
    assert c._multiline is True
    c.toggle_multiline()
    assert c._multiline is False


def test_focus_input_is_no_op() -> None:
    c = TerminalComposer()
    c.focus_input()  # must not raise


def test_history_file_enables_file_history(tmp_path: Path) -> None:
    history_file = tmp_path / "terminal_history"
    c = TerminalComposer(history_file=str(history_file))
    assert isinstance(c._session.history, FileHistory)


def test_history_file_persists_across_composer_recreation(tmp_path: Path) -> None:
    history_file = tmp_path / "terminal_history"
    first = TerminalComposer(history_file=str(history_file))
    assert isinstance(first._session.history, FileHistory)
    first._session.history.store_string("first prompt")
    first._session.history.store_string("second prompt")

    second = TerminalComposer(history_file=str(history_file))
    assert isinstance(second._session.history, FileHistory)
    assert list(second._session.history.load_history_strings()) == [
        "second prompt",
        "first prompt",
    ]


def test_multiline_paste_auto_toggles_and_inserts_text() -> None:
    c = TerminalComposer()

    class _Buffer:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def insert_text(self, text: str) -> None:
            self.calls.append(text)

    buffer = _Buffer()
    c._apply_pasted_text("line one\nline two", buffer=buffer)
    assert c._multiline is True
    assert buffer.calls == ["line one\nline two"]


def test_single_line_paste_keeps_single_line_mode() -> None:
    c = TerminalComposer()

    class _Buffer:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def insert_text(self, text: str) -> None:
            self.calls.append(text)

    buffer = _Buffer()
    c._apply_pasted_text("single line", buffer=buffer)
    assert c._multiline is False
    assert buffer.calls == ["single line"]


def test_carriage_return_paste_normalizes_to_newlines() -> None:
    c = TerminalComposer()

    class _Buffer:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def insert_text(self, text: str) -> None:
            self.calls.append(text)

    buffer = _Buffer()
    c._apply_pasted_text("line one\r\nline two\rline three", buffer=buffer)
    assert c._multiline is True
    assert buffer.calls == ["line one\nline two\nline three"]


@pytest.mark.asyncio
async def test_read_line_resets_multiline_after_submit() -> None:
    c = TerminalComposer()
    c._multiline = True

    async def _prompt_async(*args, **kwargs):
        return "hello"

    c._session = type("_Session", (), {"prompt_async": _prompt_async})()
    assert await c.read_line() == "hello"
    assert c._multiline is False


def test_slash_completer_proposes_matching_slashes() -> None:
    from prompt_toolkit.document import Document

    c = TerminalComposer(slash_commands=["/clear", "/compact", "/cost", "/exit"])
    completions = list(
        c._completer.get_completions(Document(text="/c"), complete_event=None)
    )
    texts = [comp.text for comp in completions]
    assert "/clear" in texts
    assert "/compact" in texts
    assert "/cost" in texts
    assert "/exit" not in texts


def test_slash_completer_opens_menu_for_bare_slash() -> None:
    from prompt_toolkit.document import Document

    c = TerminalComposer(slash_commands={"/model": "choose model", "/help": "show help"})
    completions = list(
        c._completer.get_completions(Document(text="/"), complete_event=None)
    )

    assert [comp.text for comp in completions] == ["/help", "/model"]
    assert completions[1].display_meta_text == "choose model"


def test_bottom_toolbar_formats_ansi_string_for_prompt_toolkit() -> None:
    c = TerminalComposer(bottom_toolbar=lambda: "\x1b[32mready\x1b[0m")

    formatted = c._formatted_bottom_toolbar()

    assert isinstance(formatted, ANSI)
