from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from openminion.cli.constants import OPENMINION_FOCUS_BELL_ENV
from openminion.cli.tui.focus.app import _DemoFocusRuntime


def _make_screen():
    from openminion.cli.tui.focus.screen import FocusScreen

    runtime = _DemoFocusRuntime(working_dir="/tmp", session="bell-test")
    return FocusScreen(runtime=runtime, working_dir="/tmp")


def test_bell_suppressed_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENMINION_FOCUS_BELL_ENV, raising=False)
    screen = _make_screen()
    buf = io.StringIO()
    with redirect_stdout(buf):
        screen._on_turn_complete(elapsed_seconds=20.0)
    assert "\a" not in buf.getvalue()


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
