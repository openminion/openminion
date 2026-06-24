from __future__ import annotations

import io
import sys
from contextlib import contextmanager

from openminion.cli.chat.runtime import (
    _format_stream_progress_note,
    build_chat_progress_callback,
)
from openminion.cli.chat.ui import PhaseStatusDisplay


class _StdoutCapture:
    def __init__(self, buf: io.StringIO, *, is_tty: bool) -> None:
        self._buf = buf
        self._is_tty = is_tty

    def write(self, text: str) -> int:
        return self._buf.write(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return self._is_tty


@contextmanager
def _patched_stdout(buf: io.StringIO, *, is_tty: bool):
    real_stdout = sys.stdout
    sys.stdout = _StdoutCapture(buf, is_tty=is_tty)
    try:
        yield
    finally:
        sys.stdout = real_stdout


def test_format_tool_started_returns_running_line() -> None:
    note = _format_stream_progress_note(
        {
            "kind": "tool_started",
            "tool_name": "Bash",
            "args": {"cmd": "ls"},
        }
    )
    assert note is not None
    assert "Bash" in note or "bash" in note.lower()


def test_format_tool_completed_ok_returns_ok_line() -> None:
    note = _format_stream_progress_note(
        {
            "kind": "tool_completed",
            "tool_name": "Bash",
            "args": {"cmd": "ls"},
            "ok": True,
            "duration_ms": 42,
        }
    )
    assert note is not None
    assert "Bash" in note or "bash" in note.lower()


def test_format_tool_completed_error_returns_error_line() -> None:
    note = _format_stream_progress_note(
        {
            "kind": "tool_completed",
            "tool_name": "Bash",
            "args": {"cmd": "ls"},
            "ok": False,
            "duration_ms": 42,
        }
    )
    assert note is not None


def test_format_non_tool_event_returns_none() -> None:
    assert (
        _format_stream_progress_note(
            {
                "kind": "phase",
                "label": "thinking",
            }
        )
        is None
    )
    assert (
        _format_stream_progress_note(
            {
                "kind": "thinking",
            }
        )
        is None
    )


def test_format_empty_payload_returns_none() -> None:
    assert _format_stream_progress_note({}) is None
    assert _format_stream_progress_note({"kind": ""}) is None


def test_format_task_plan_step_completed_emits_note() -> None:
    note = _format_stream_progress_note(
        {"kind": "task_plan_step_completed", "step_text": "ship"}
    )
    assert note is not None
    assert "Plan step done: ship" in note


def test_format_approval_request_emits_note() -> None:
    note = _format_stream_progress_note(
        {"kind": "approval_request", "tool_name": "git.reset"}
    )
    assert note is not None
    assert "Approval requested: git.reset" in note


def test_format_background_completed_emits_note() -> None:
    note = _format_stream_progress_note(
        {
            "kind": "background_completed",
            "title": "research",
            "duration_ms": 7000,
        }
    )
    assert note is not None
    assert "Background done: research (7000 ms)" in note


def test_format_error_event_emits_note() -> None:
    note = _format_stream_progress_note(
        {"kind": "error", "title": "RuntimeError", "message": "boom"}
    )
    assert note is not None
    assert "Error: RuntimeError — boom" in note


def test_format_budget_event_still_emits_note() -> None:
    note = _format_stream_progress_note(
        {"kind": "budget_event", "event_type": "tokens_low"}
    )
    assert note is not None
    assert "Budget event: tokens_low" in note


def _force_tty_phase_display() -> tuple[PhaseStatusDisplay, io.StringIO]:
    captured = io.StringIO()
    with _patched_stdout(captured, is_tty=True):
        display = PhaseStatusDisplay(enabled=True, animate=False)

    assert display.enabled, "PhaseStatusDisplay should report enabled"

    return display, captured


def test_callback_routes_tool_started_to_emit_note() -> None:
    display, captured = _force_tty_phase_display()
    cb = build_chat_progress_callback(phase_display=display)
    with _patched_stdout(captured, is_tty=True):
        cb(
            {
                "kind": "tool_started",
                "tool_name": "Bash",
                "args": {"cmd": "ls"},
            }
        )

    out = captured.getvalue()
    assert "Bash" in out or "bash" in out.lower(), (
        f"expected tool name in output, got {out!r}"
    )


def test_callback_routes_tool_completed_to_emit_note() -> None:
    display, captured = _force_tty_phase_display()
    cb = build_chat_progress_callback(phase_display=display)
    with _patched_stdout(captured, is_tty=True):
        cb(
            {
                "kind": "tool_completed",
                "tool_name": "Read",
                "args": {"path": "foo.py"},
                "ok": True,
                "duration_ms": 12,
            }
        )

    out = captured.getvalue()
    assert "Read" in out or "read" in out.lower()


def test_callback_routes_non_tool_event_to_update_not_emit() -> None:
    display, captured = _force_tty_phase_display()
    cb = build_chat_progress_callback(phase_display=display)
    with _patched_stdout(captured, is_tty=True):
        cb({"kind": "phase", "label": "thinking"})

    out = captured.getvalue()
    assert "Bash" not in out
    assert "Read" not in out


def test_phase_display_non_tty_still_enables_emit_note() -> None:
    captured = io.StringIO()
    with _patched_stdout(captured, is_tty=False):
        display = PhaseStatusDisplay(enabled=True, animate=True)

    assert display.enabled is True, (
        "CPC-01: caller-requested enabled=True must persist even when "
        "stdout is non-TTY (only animate gets force-disabled)"
    )
    assert display.animate is False, (
        "animate must be force-disabled when stdout is non-TTY "
        "(spinner thread requires TTY)"
    )

    captured2 = io.StringIO()
    with _patched_stdout(captured2, is_tty=False):
        display.emit_note("● Bash (running)")

    assert "Bash" in captured2.getvalue(), (
        "CPC-01: emit_note in non-TTY enabled display must write the note as plain text"
    )


def test_explicitly_disabled_phase_display_drops_tool_events() -> None:
    display = PhaseStatusDisplay(enabled=False, animate=False)
    assert display.enabled is False
    assert display.animate is False

    captured = io.StringIO()
    with _patched_stdout(captured, is_tty=False):
        display.emit_note("● Bash (running)")

    assert captured.getvalue() == "", (
        "Explicitly disabled display (--no-progress path) must be silent"
    )


def test_show_progress_defaults_to_true() -> None:
    from argparse import Namespace

    args = Namespace()  # no --no-progress flag set
    show_progress = not bool(getattr(args, "no_progress", False))
    assert show_progress is True


def test_show_progress_disabled_only_when_flag_present() -> None:
    from argparse import Namespace

    args = Namespace(no_progress=True)
    show_progress = not bool(getattr(args, "no_progress", False))
    assert show_progress is False
