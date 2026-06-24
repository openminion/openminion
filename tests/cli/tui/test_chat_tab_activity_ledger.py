from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from openminion.cli.tui.presentation.models import MessageKind
from openminion.cli.tui.tabs.chat import ChatTab


def _bare_chat_tab() -> tuple[ChatTab, list[Any]]:
    tab = object.__new__(ChatTab)
    pushed: list[Any] = []
    return tab, pushed


def _mock_app_with_passthrough_call_from_thread() -> MagicMock:
    app = MagicMock()
    app.call_from_thread = lambda fn, *args, **kwargs: fn(*args, **kwargs)
    return app


def test_dashboard_push_activity_row_renders_plan_step_done() -> None:
    tab, pushed = _bare_chat_tab()
    app = _mock_app_with_passthrough_call_from_thread()
    chat = SimpleNamespace(push_message=lambda msg: pushed.append(msg))
    result = tab._dashboard_push_activity_row(
        app, chat, {"kind": "task_plan_step_completed", "step_text": "ship"}
    )
    assert result is True
    assert len(pushed) == 1
    assert pushed[0].kind == MessageKind.SYSTEM
    assert "Plan step done: ship" in pushed[0].body


def test_dashboard_push_activity_row_renders_error_as_error_kind() -> None:
    tab, pushed = _bare_chat_tab()
    app = _mock_app_with_passthrough_call_from_thread()
    chat = SimpleNamespace(push_message=lambda msg: pushed.append(msg))
    result = tab._dashboard_push_activity_row(
        app, chat, {"kind": "error", "title": "RuntimeError", "message": "boom"}
    )
    assert result is True
    assert pushed[0].kind == MessageKind.ERROR
    assert "Error: RuntimeError — boom" in pushed[0].body


def test_dashboard_push_activity_row_renders_budget_event() -> None:
    tab, pushed = _bare_chat_tab()
    app = _mock_app_with_passthrough_call_from_thread()
    chat = SimpleNamespace(push_message=lambda msg: pushed.append(msg))
    result = tab._dashboard_push_activity_row(
        app, chat, {"kind": "budget_event", "event_type": "tokens_low"}
    )
    assert result is True
    assert pushed[0].kind == MessageKind.SYSTEM
    assert "Budget event: tokens_low" in pushed[0].body


def test_dashboard_push_activity_row_skips_tool_events() -> None:
    tab, pushed = _bare_chat_tab()
    app = _mock_app_with_passthrough_call_from_thread()
    chat = SimpleNamespace(push_message=lambda msg: pushed.append(msg))
    result = tab._dashboard_push_activity_row(
        app, chat, {"kind": "tool_started", "tool_name": "bash"}
    )
    assert result is False
    assert pushed == []


def test_dashboard_push_activity_row_skips_generic_status_payload() -> None:
    tab, pushed = _bare_chat_tab()
    app = _mock_app_with_passthrough_call_from_thread()
    chat = SimpleNamespace(push_message=lambda msg: pushed.append(msg))
    result = tab._dashboard_push_activity_row(
        app, chat, {"trace_id": "t1", "status_key": "executing", "label": "Working"}
    )
    assert result is False
    assert pushed == []


def test_dashboard_push_activity_row_falls_back_when_call_from_thread_fails() -> None:
    tab, pushed = _bare_chat_tab()
    app = MagicMock()
    app.call_from_thread.side_effect = RuntimeError("no app loop")
    chat = SimpleNamespace(push_message=lambda msg: pushed.append(msg))
    result = tab._dashboard_push_activity_row(
        app, chat, {"kind": "error", "title": "x", "message": "y"}
    )
    assert result is True
    assert len(pushed) == 1
