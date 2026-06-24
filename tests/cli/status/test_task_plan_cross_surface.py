from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import MagicMock

from rich.console import Console

from openminion.cli.chat.runtime import _format_stream_progress_note
from openminion.cli.status.activity_ledger import (
    KIND_PLAN,
    activity_from_progress_payload,
)
from openminion.cli.tui.focus.screen import FocusScreen
from openminion.cli.tui.terminal.transcript import TerminalTranscript
from openminion.cli.tui.presentation.models import MessageKind
from openminion.cli.tui.tabs.chat import ChatTab


_FULL_PLAN_PAYLOAD = {
    "kind": "task_plan",
    "plan": {
        "summary": "Smoke",
        "items": [
            {"text": "lint", "status": "done"},
            {"text": "test", "status": "in_progress"},
            {"text": "ship", "status": "todo"},
        ],
    },
}

_STEP_COMPLETED_PAYLOAD = {
    "kind": "task_plan_step_completed",
    "step_text": "lint",
}

_STEP_BLOCKED_PAYLOAD = {
    "kind": "task_plan_step_blocked",
    "step_text": "ship",
    "reason": "missing token",
}


def test_task_plan_payload_routes_to_plan_kind() -> None:
    event = activity_from_progress_payload(_FULL_PLAN_PAYLOAD)
    assert event is not None and event.kind == KIND_PLAN
    assert event.plan["summary"] == "Smoke"


def test_task_plan_step_completed_routes_to_plan_kind() -> None:
    event = activity_from_progress_payload(_STEP_COMPLETED_PAYLOAD)
    assert event is not None and event.kind == KIND_PLAN


def test_chat_cli_renders_plan_full_render() -> None:
    note = _format_stream_progress_note(_FULL_PLAN_PAYLOAD)
    assert note is not None
    assert "Plan" in note
    assert "lint" in note and "test" in note and "ship" in note


def test_chat_cli_renders_plan_step_done_one_line() -> None:
    note = _format_stream_progress_note(_STEP_COMPLETED_PAYLOAD)
    assert note == "Plan step done: lint"


def test_chat_cli_renders_plan_step_blocked_with_reason() -> None:
    note = _format_stream_progress_note(_STEP_BLOCKED_PAYLOAD)
    assert note == "Plan step blocked: ship — missing token"


def test_terminal_flow_pushes_durable_plan_row() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, no_color=True)
    transcript = TerminalTranscript(console, verbosity="normal")
    event = activity_from_progress_payload(_FULL_PLAN_PAYLOAD)
    transcript.push_activity_event(event)
    out = buf.getvalue()
    assert "Plan" in out
    assert "lint" in out
    assert "[x] lint" in out


def test_terminal_flow_pushes_plan_step_blocked_line() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, no_color=True)
    transcript = TerminalTranscript(console, verbosity="normal")
    event = activity_from_progress_payload(_STEP_BLOCKED_PAYLOAD)
    transcript.push_activity_event(event)
    assert "Plan step blocked: ship — missing token" in buf.getvalue()


def test_focus_rich_pushes_durable_plan_row() -> None:
    screen = object.__new__(FocusScreen)
    pushed: list = []
    transcript_mock = SimpleNamespace(
        push_message=lambda msg: pushed.append(msg) or msg,
    )
    screen.query_one = MagicMock(return_value=transcript_mock)  # type: ignore[attr-defined]
    result = screen._push_durable_activity_row(_FULL_PLAN_PAYLOAD)
    assert result is True
    assert len(pushed) == 1
    msg = pushed[0]
    assert msg.kind == MessageKind.SYSTEM
    assert "Plan" in msg.body
    assert "[x] lint" in msg.body


def test_dashboard_chat_tab_pushes_durable_plan_row() -> None:
    tab = object.__new__(ChatTab)
    pushed: list = []
    app = MagicMock()
    app.call_from_thread = lambda fn, *args, **kwargs: fn(*args, **kwargs)
    chat = SimpleNamespace(push_message=lambda msg: pushed.append(msg))
    result = tab._dashboard_push_activity_row(app, chat, _FULL_PLAN_PAYLOAD)
    assert result is True
    assert len(pushed) == 1
    msg = pushed[0]
    assert msg.kind == MessageKind.SYSTEM
    assert "Plan" in msg.body


def test_runtime_emission_of_task_plan_events_is_documented_followup() -> None:
    assert activity_from_progress_payload(_FULL_PLAN_PAYLOAD) is not None
