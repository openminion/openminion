from __future__ import annotations

from pathlib import Path

import pytest

from openminion.cli.theme import DARK, LIGHT
from openminion.cli.tui.app import OpenMinionApp
from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime


@pytest.mark.asyncio
async def test_dashboard_honors_theme_kwarg() -> None:
    app = OpenMinionApp(theme=LIGHT)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.active_theme is LIGHT
        variables = app.stylesheet._variables
        assert variables["openminion-chat-user-bg"] == LIGHT.chat_user_bg
        # Sanity: LIGHT and DARK actually differ on this token.
        assert LIGHT.chat_user_bg != DARK.chat_user_bg


@pytest.mark.asyncio
async def test_focus_honors_theme_kwarg(tmp_path: Path) -> None:
    app = FocusApp(
        runtime=_DemoFocusRuntime(working_dir=str(tmp_path)),
        working_dir=str(tmp_path),
        theme=LIGHT,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.active_theme is LIGHT
        variables = app.stylesheet._variables
        assert variables["openminion-chat-user-bg"] == LIGHT.chat_user_bg


@pytest.mark.asyncio
async def test_dashboard_default_still_dark_without_kwarg() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.active_theme is DARK


@pytest.mark.asyncio
async def test_dashboard_invalid_theme_kwarg_falls_back_to_dark() -> None:
    app = OpenMinionApp(theme="not-a-theme")  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.active_theme is DARK
