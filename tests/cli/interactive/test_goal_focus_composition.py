from __future__ import annotations

import tempfile

import pytest

from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime
from openminion.cli.interactive.runtime import OpenMinionRuntime
from openminion.cli.interactive.screen import FocusScreen
from openminion.cli.interactive.widgets import FocusTranscript
from openminion.cli.presentation.models import MessageKind


def _last_system_body(transcript: FocusTranscript) -> str:
    for message in reversed(transcript._messages):
        if message.kind in {MessageKind.SYSTEM, MessageKind.ERROR}:
            return str(message.body)
    return ""


@pytest.mark.asyncio
async def test_goal_slash_uses_runtime_owner_and_refreshes_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp)
        calls: list[str] = []
        runtime.execute_goal_command = lambda line: (
            calls.append(line) or "info",
            "goal=goal-1\nstatus=active",
        )
        runtime.goal_statusline_label = lambda: "goal: active turn 0 · started"
        app = FocusApp(runtime=runtime, working_dir=tmp)

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, FocusScreen)

            screen._handle_command("/goal status")
            await pilot.pause()

            transcript = screen.query_one(FocusTranscript)
            assert calls == ["/goal status"]
            assert "goal=goal-1" in _last_system_body(transcript)
            assert "/goal" in {
                aliases[0]
                for aliases, _description, _handler in screen._slash_command_registry
            }


@pytest.mark.asyncio
async def test_goal_slash_reports_missing_runtime_owner() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp)
        app = FocusApp(runtime=runtime, working_dir=tmp)

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, FocusScreen)

            screen._handle_command("/goal status")
            await pilot.pause()

            transcript = screen.query_one(FocusTranscript)
            assert "does not expose goal commands" in _last_system_body(transcript)


def test_goal_runtime_adapter_requires_bound_session() -> None:
    runtime = OpenMinionRuntime.__new__(OpenMinionRuntime)
    runtime._session_id = None

    assert runtime.execute_goal_command("/goal status") == (
        "error",
        "No active session for /goal.",
    )
    assert runtime.goal_statusline_label() == ""
