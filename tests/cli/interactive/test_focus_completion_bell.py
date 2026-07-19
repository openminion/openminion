from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from openminion.cli.constants import OPENMINION_FOCUS_BELL_ENV
from openminion.cli.interactive.app import _DemoFocusRuntime
from openminion.cli.interactive.screen import FocusScreen, _format_response_time
from openminion.cli.interactive.widgets import FocusTranscript
from openminion.cli.presentation.models import ChatMessage, MessageKind


def _make_screen():
    runtime = _DemoFocusRuntime(working_dir="/tmp", session="bell-test")
    return FocusScreen(runtime=runtime, working_dir="/tmp")


class _TranscriptProbe:
    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    def push_message(self, message: ChatMessage) -> None:
        self.messages.append(message)


def _attach_transcript_probe(
    monkeypatch: pytest.MonkeyPatch, screen: FocusScreen
) -> _TranscriptProbe:
    probe = _TranscriptProbe()

    def query_one(selector):
        if selector is FocusTranscript:
            return probe
        raise LookupError(selector)

    monkeypatch.setattr(screen, "query_one", query_one)
    return probe


def test_bell_suppressed_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENMINION_FOCUS_BELL_ENV, raising=False)
    screen = _make_screen()
    buf = io.StringIO()
    with redirect_stdout(buf):
        screen._on_turn_complete(elapsed_seconds=20.0)
    assert "\a" not in buf.getvalue()


def test_focus_completion_adds_muted_timing_message_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENMINION_SHOW_RESPONSE_TIME", raising=False)
    screen = _make_screen()
    probe = _attach_transcript_probe(monkeypatch, screen)

    screen._on_turn_complete(elapsed_seconds=82.0)

    assert len(probe.messages) == 1
    message = probe.messages[0]
    assert message.kind == MessageKind.SYSTEM
    assert message.body == "Done in 1m22s"


def test_focus_completion_can_hide_timing_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_SHOW_RESPONSE_TIME", "0")
    screen = _make_screen()
    probe = _attach_transcript_probe(monkeypatch, screen)

    screen._on_turn_complete(elapsed_seconds=82.0)

    assert probe.messages == []


def test_focus_response_time_format_uses_whole_seconds() -> None:
    assert _format_response_time(0.0) == "0s"
    assert _format_response_time(0.9) == "<1s"
    assert _format_response_time(3.8) == "3s"
    assert _format_response_time(62.5) == "1m02s"


def test_bell_fires_when_env_on_and_elapsed_over_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENMINION_FOCUS_BELL_ENV, "1")
    screen = _make_screen()
    buf = io.StringIO()
    with redirect_stdout(buf):
        screen._on_turn_complete(elapsed_seconds=15.0)
    assert "\a" in buf.getvalue()


@pytest.mark.parametrize("truthy", ["1", "true", "on", "yes", "TRUE", "On"])
def test_truthy_env_values_enable_bell(
    monkeypatch: pytest.MonkeyPatch, truthy: str
) -> None:
    monkeypatch.setenv(OPENMINION_FOCUS_BELL_ENV, truthy)
    screen = _make_screen()
    buf = io.StringIO()
    with redirect_stdout(buf):
        screen._on_turn_complete(elapsed_seconds=15.0)
    assert "\a" in buf.getvalue()


def test_bell_suppressed_when_elapsed_at_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENMINION_FOCUS_BELL_ENV, "1")
    screen = _make_screen()
    buf = io.StringIO()
    with redirect_stdout(buf):
        screen._on_turn_complete(elapsed_seconds=10.0)
    assert "\a" not in buf.getvalue()


def test_bell_suppressed_when_elapsed_under_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENMINION_FOCUS_BELL_ENV, "1")
    screen = _make_screen()
    buf = io.StringIO()
    with redirect_stdout(buf):
        screen._on_turn_complete(elapsed_seconds=2.5)
    assert "\a" not in buf.getvalue()


@pytest.mark.parametrize("falsey", ["0", "false", "off", "no", ""])
def test_falsey_env_values_keep_bell_off(
    monkeypatch: pytest.MonkeyPatch, falsey: str
) -> None:
    monkeypatch.setenv(OPENMINION_FOCUS_BELL_ENV, falsey)
    screen = _make_screen()
    buf = io.StringIO()
    with redirect_stdout(buf):
        screen._on_turn_complete(elapsed_seconds=20.0)
    assert "\a" not in buf.getvalue()


def test_bell_helper_handles_invalid_elapsed_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENMINION_FOCUS_BELL_ENV, "1")
    screen = _make_screen()
    buf = io.StringIO()
    with redirect_stdout(buf):
        screen._on_turn_complete(elapsed_seconds="not a number")  # type: ignore[arg-type]
    assert "\a" not in buf.getvalue()
