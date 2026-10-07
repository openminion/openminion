from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

import openminion.cli.interactive.terminal.composer as composer_module
from openminion.cli.interactive.terminal.composer import (
    TerminalComposer,
    edit_external_draft,
)
from openminion.cli.interactive.terminal.shell import (
    _TerminalFocusLoop,
    _configured_editor,
)
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript


def test_external_editor_uses_argument_vector_and_removes_scratch(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def _run(argv: list[str], *, check: bool) -> SimpleNamespace:
        captured["argv"] = argv
        captured["check"] = check
        draft_path = Path(argv[-1])
        captured["draft_path"] = draft_path
        assert draft_path.read_text(encoding="utf-8") == "original"
        draft_path.write_text("edited\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(composer_module.subprocess, "run", _run)

    assert edit_external_draft("original", "code --wait --reuse-window") == "edited\n"
    assert captured["argv"][:-1] == ["code", "--wait", "--reuse-window"]
    assert captured["check"] is True
    assert not Path(captured["draft_path"]).exists()


def test_external_editor_failure_does_not_return_a_draft(monkeypatch) -> None:
    errors: list[str] = []
    composer = TerminalComposer(
        editor_command="missing-editor",
        on_editor_error=errors.append,
    )

    async def _run_in_terminal(*_args: object, **_kwargs: object) -> str:
        raise FileNotFoundError("missing-editor")

    monkeypatch.setattr(composer_module, "run_in_terminal", _run_in_terminal)

    assert asyncio.run(composer.edit_draft("keep me")) is None
    assert errors == ["missing-editor"]


def test_external_editor_preserves_an_unchanged_trailing_newline(monkeypatch) -> None:
    def _run(argv: list[str], *, check: bool) -> SimpleNamespace:
        assert check is True
        assert Path(argv[-1]).read_text(encoding="utf-8") == "keep me\n"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(composer_module.subprocess, "run", _run)

    assert edit_external_draft("keep me\n", "editor") == "keep me\n"


def test_missing_editor_reports_configuration_without_losing_text() -> None:
    errors: list[str] = []
    composer = TerminalComposer(on_editor_error=errors.append)

    assert asyncio.run(composer.edit_draft("keep me")) is None
    assert errors == ["set VISUAL or EDITOR to choose an editor"]


def test_composer_prefills_saved_editor_draft_without_auto_submit() -> None:
    captured: dict[str, object] = {}
    composer = TerminalComposer()
    composer.prefill_draft("line one\nline two")

    async def _prompt_async(*_args: object, **kwargs: object) -> str:
        captured.update(kwargs)
        return str(kwargs["default"])

    composer._session = SimpleNamespace(prompt_async=_prompt_async)

    assert asyncio.run(composer.read_line()) == "line one\nline two"
    assert captured["default"] == "line one\nline two"
    assert "accept_default" not in captured
    assert composer._next_draft is None


def test_live_editor_cancel_preserves_composer_buffer(monkeypatch) -> None:
    composer = TerminalComposer(editor_command="editor")
    buffer = SimpleNamespace(text="keep me", document=None)
    event = SimpleNamespace(app=SimpleNamespace(current_buffer=buffer))

    async def _cancel(_text: str) -> None:
        return None

    monkeypatch.setattr(composer, "edit_draft", _cancel)

    asyncio.run(composer._edit_live_draft(event))

    assert buffer.text == "keep me"
    assert buffer.document is None


def test_live_editor_save_replaces_buffer_without_submitting(monkeypatch) -> None:
    composer = TerminalComposer(editor_command="editor")
    buffer = SimpleNamespace(text="before", document=None)
    event = SimpleNamespace(app=SimpleNamespace(current_buffer=buffer))

    async def _save(_text: str) -> str:
        return "after"

    monkeypatch.setattr(composer, "edit_draft", _save)

    asyncio.run(composer._edit_live_draft(event))

    assert buffer.document.text == "after"
    assert buffer.document.cursor_position == len("after")


def test_idle_editor_save_prefills_next_read_without_starting_turn(monkeypatch) -> None:
    class _Composer:
        def __init__(self) -> None:
            self.drafts: list[str] = []

        async def edit_draft(self, text: str) -> str:
            assert text == ""
            return "draft from editor"

        def prefill_draft(self, text: str) -> None:
            self.drafts.append(text)

    composer = _Composer()
    console = Console(force_terminal=False)
    transcript = TerminalTranscript(console, plain_spinner=True)
    loop = _TerminalFocusLoop(
        runtime=SimpleNamespace(),
        console=console,
        transcript=transcript,
        status_line=TerminalStatusLine(),
        composer=composer,
        overlay=object(),
        working_dir=".",
        custom_commands={},
    )
    reads: list[bool] = []
    monkeypatch.setattr(loop, "start_read_task", lambda: reads.append(True))

    assert asyncio.run(loop.handle_idle_input("/editor")) is None
    assert composer.drafts == ["draft from editor"]
    assert reads == [True]
    assert loop.active_turn_task is None


def test_configured_editor_prefers_visual_and_reads_runtime_env(monkeypatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            runtime=SimpleNamespace(env={"VISUAL": "code --wait", "EDITOR": "vim"})
        )
    )

    assert _configured_editor(runtime) == "code --wait"
