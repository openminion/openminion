from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from openminion.cli.tui.focus.screen import FocusScreen
from openminion.cli.tui.presentation.models import MessageKind


def _bare_screen() -> tuple[FocusScreen, list[Any]]:
    screen = object.__new__(FocusScreen)
    pushed_messages: list[Any] = []

    transcript_mock = SimpleNamespace(
        push_message=lambda msg: pushed_messages.append(msg) or msg,
    )
    screen.query_one = MagicMock(return_value=transcript_mock)  # type: ignore[attr-defined]
    return screen, pushed_messages


def test_push_durable_activity_row_renders_plan_event() -> None:
    screen, pushed = _bare_screen()
    result = screen._push_durable_activity_row(
        {"kind": "task_plan_step_completed", "step_text": "ship"}
    )
    assert result is True
    assert len(pushed) == 1
    msg = pushed[0]
    assert msg.kind == MessageKind.SYSTEM
    assert "Plan step done: ship" in msg.body


def test_push_durable_activity_row_renders_approval_denied_as_error_kind() -> None:
    screen, pushed = _bare_screen()
    result = screen._push_durable_activity_row(
        {
            "kind": "approval_decision",
            "tool_name": "git.reset",
            "decision": "denied",
            "reason": "outside workspace",
        }
    )
    assert result is True
    msg = pushed[0]
    assert msg.kind == MessageKind.SYSTEM
    assert "Approval denied: git.reset — outside workspace" in msg.body


def test_push_durable_activity_row_renders_error_event_with_error_kind() -> None:
    screen, pushed = _bare_screen()
    result = screen._push_durable_activity_row(
        {"kind": "error", "title": "RuntimeError", "message": "boom"}
    )
    assert result is True
    msg = pushed[0]
    assert msg.kind == MessageKind.ERROR
    assert "Error: RuntimeError — boom" in msg.body


def test_push_durable_activity_row_renders_background_event() -> None:
    screen, pushed = _bare_screen()
    result = screen._push_durable_activity_row(
        {
            "kind": "background_completed",
            "title": "research",
            "duration_ms": 7000,
        }
    )
    assert result is True
    msg = pushed[0]
    assert msg.kind == MessageKind.SYSTEM
    assert "Background done: research (7000 ms)" in msg.body


def test_push_durable_activity_row_renders_budget_event() -> None:
    screen, pushed = _bare_screen()
    result = screen._push_durable_activity_row(
        {"kind": "budget_event", "event_type": "tokens_low"}
    )
    assert result is True
    msg = pushed[0]
    assert msg.kind == MessageKind.SYSTEM
    assert "Budget event: tokens_low" in msg.body


def test_push_durable_activity_row_skips_tool_events() -> None:
    screen, pushed = _bare_screen()
    result = screen._push_durable_activity_row(
        {"kind": "tool_started", "tool_name": "bash", "args": {"command": "ls"}}
    )
    assert result is False
    assert pushed == []


def test_push_durable_activity_row_skips_generic_status_payload() -> None:
    screen, pushed = _bare_screen()
    result = screen._push_durable_activity_row(
        {
            "trace_id": "t1",
            "status_key": "executing",
            "label": "Working",
        }
    )
    assert result is False
    assert pushed == []


def test_push_durable_activity_row_safe_on_empty_payload() -> None:
    screen, pushed = _bare_screen()
    assert screen._push_durable_activity_row({}) is False
    assert pushed == []
