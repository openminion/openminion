from __future__ import annotations

import pathlib

import pytest

from openminion.cli.tui.presentation.contracts import (
    Composer,
    OverlayPresenter,
    StatusLine,
    TranscriptSink,
    TurnHandleProtocol,
)
from openminion.cli.tui.presentation.models import ChatMessage, ToolEvent


class _StubTurnHandle:
    def append_token(self, s: str) -> None:
        pass

    def append_tool_block(self, event: ToolEvent) -> None:
        pass

    def complete(self, final_text: str | None = None) -> None:
        pass


class _StubTranscript:
    def begin_turn(self, role="assistant"):
        return _StubTurnHandle()

    def push_message(self, message: ChatMessage):
        return None

    def set_messages(self, messages):
        pass

    def clear_messages(self):
        pass

    def filter_messages(self, query: str):
        pass

    def copy_selected_message(self):
        return None

    def copy_last_copyable_message(self):
        return None

    def drop_message(self, msg_id: str):
        return False


class _StubComposer:
    def set_resumed(self, is_resumed):
        pass

    def set_disabled(self, disabled):
        pass

    def focus_input(self):
        pass

    def toggle_multiline(self):
        pass


class _StubStatusLine:
    def set_state(self, **segments):
        pass


class _StubOverlay:
    def present_resume_picker(self, sessions):
        return None

    def present_approval(self, prompt):
        return "allow"

    def present_completion(self, message):
        return ""


def test_stub_turn_handle_satisfies_protocol() -> None:
    assert isinstance(_StubTurnHandle(), TurnHandleProtocol)


def test_stub_transcript_satisfies_protocol() -> None:
    assert isinstance(_StubTranscript(), TranscriptSink)


def test_stub_composer_satisfies_protocol() -> None:
    assert isinstance(_StubComposer(), Composer)


def test_stub_status_line_satisfies_protocol() -> None:
    assert isinstance(_StubStatusLine(), StatusLine)


def test_stub_overlay_satisfies_protocol() -> None:
    assert isinstance(_StubOverlay(), OverlayPresenter)


def test_object_does_not_satisfy_transcript_protocol() -> None:
    assert not isinstance(object(), TranscriptSink)


def test_partial_implementation_fails_protocol_check() -> None:
    class Partial:
        def push_message(self, m):
            return None

    assert not isinstance(Partial(), TranscriptSink)


def test_contracts_module_does_not_import_textual_or_prompt_toolkit() -> None:
    src = (
        pathlib.Path(__file__).resolve().parents[4]
        / "src"
        / "openminion"
        / "cli"
        / "tui"
        / "presentation"
        / "contracts.py"
    ).read_text(encoding="utf-8")
    assert "import textual" not in src
    assert "from textual" not in src
    assert "import prompt_toolkit" not in src
    assert "from prompt_toolkit" not in src


@pytest.mark.asyncio
async def test_focus_transcript_satisfies_transcript_sink_protocol() -> None:
    from textual.app import App
    from textual.containers import Vertical

    from openminion.cli.tui.focus.widgets import FocusTranscript

    class _Harness(App):
        def compose(self):
            self.transcript = FocusTranscript()
            yield Vertical(self.transcript)

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        t = pilot.app.transcript
        assert isinstance(t, TranscriptSink), (
            "FocusTranscript must satisfy TranscriptSink protocol"
        )


@pytest.mark.asyncio
async def test_focus_composer_satisfies_composer_protocol() -> None:
    from textual.app import App
    from textual.containers import Vertical

    from openminion.cli.tui.focus.widgets import FocusComposer

    class _Harness(App):
        def compose(self):
            self.composer = FocusComposer()
            yield Vertical(self.composer)

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        assert isinstance(pilot.app.composer, Composer)


def test_focus_status_line_satisfies_status_line_protocol() -> None:
    from openminion.cli.tui.focus.widgets import FocusStatusLine

    line = FocusStatusLine()
    assert isinstance(line, StatusLine)
