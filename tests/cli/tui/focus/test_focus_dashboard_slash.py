from __future__ import annotations

from pathlib import Path

import pytest

from openminion.cli.tui.focus.app import FocusApp, _DemoFocusRuntime
from openminion.cli.tui.focus.screen import FocusScreen
from openminion.cli.tui.focus.widgets import FocusTranscript
from openminion.cli.tui.presentation.models import MessageKind


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
async def test_dashboard_slash_attempts_push_or_surfaces_inline_message(
    tmp_path: Path,
) -> None:
    app = _make_app(str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        pushed: list[object] = []

        real_push = app.push_screen

        def _capture_push(screen):
            pushed.append(screen)
            return real_push(screen)

        app.push_screen = _capture_push  # type: ignore[method-assign]

        assert isinstance(app.screen, FocusScreen)
        focus_screen = app.screen
        focus_screen._slash_dashboard("")
        await pilot.pause()

        chat = focus_screen.query_one(FocusTranscript)
        inline_messages = [
            m
            for m in chat._messages
            if m.kind == MessageKind.SYSTEM and "Dashboard" in m.body
        ]
        assert (len(pushed) == 1) ^ (len(inline_messages) == 1), (
            f"expected exactly one of (push, inline message); got "
            f"pushed={len(pushed)} inline={len(inline_messages)}"
        )


@pytest.mark.asyncio
async def test_dashboard_slash_surfaces_error_inline_on_import_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _make_app(str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()

        import builtins

        real_import = builtins.__import__

        def _fail_import(name, *args, **kwargs):
            if name == "openminion.cli.tui.screen":
                raise ImportError("dashboard module simulated missing")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fail_import)

        assert isinstance(app.screen, FocusScreen)
        app.screen._slash_dashboard("")
        await pilot.pause()

        chat = app.screen.query_one(FocusTranscript)
        system_msgs = [
            m
            for m in chat._messages
            if m.kind == MessageKind.SYSTEM and "Dashboard unavailable" in m.body
        ]
        assert system_msgs, (
            "expected an inline system message when dashboard import fails"
        )
