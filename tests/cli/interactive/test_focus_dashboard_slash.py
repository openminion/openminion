from __future__ import annotations

from pathlib import Path

import pytest

from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime
from openminion.cli.interactive.screen import FocusScreen
from openminion.cli.interactive.widgets import FocusTranscript
from openminion.cli.presentation.models import MessageKind


def _make_app(tmp: str) -> FocusApp:
    runtime = _DemoFocusRuntime(working_dir=tmp)
    return FocusApp(runtime=runtime, working_dir=tmp)


@pytest.mark.asyncio
async def test_dashboard_slash_registered_with_help_text(tmp_path: Path) -> None:
    app = _make_app(str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, FocusScreen)
        registry = screen._slash_command_registry
        dashboard_entries = [entry for entry in registry if "/dashboard" in entry[0]]
        assert len(dashboard_entries) == 1
        aliases, description, handler = dashboard_entries[0]
        assert aliases == ("/dashboard",)
        assert "dashboard" in description.lower()
        assert handler == "_slash_dashboard"


@pytest.mark.asyncio
async def test_dashboard_slash_surfaces_retirement_notice(
    tmp_path: Path,
) -> None:
    app = _make_app(str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, FocusScreen)
        focus_screen = app.screen
        focus_screen._slash_dashboard("")
        await pilot.pause()

        chat = focus_screen.query_one(FocusTranscript)
        inline_messages = [
            m
            for m in chat._messages
            if m.kind == MessageKind.SYSTEM and "dashboard was retired" in m.body
        ]
        assert len(inline_messages) == 1
        assert "openminion status" in inline_messages[0].body
