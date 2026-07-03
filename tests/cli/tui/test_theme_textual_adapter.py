from __future__ import annotations

import pytest

from openminion.cli.theme import DARK, LIGHT
from openminion.cli.theme.textual_adapter import as_tcss_preamble
from openminion.cli.tui.app import OpenMinionApp
from openminion.cli.tui.focus.app import FocusApp, _DemoFocusRuntime


def test_preamble_declares_openminion_prefixed_variables() -> None:
    text = as_tcss_preamble(LIGHT)
    assert "$openminion-chat-user-bg:" in text
    assert "$openminion-surface-app-bg:" in text
    assert "$openminion-text-primary:" in text
    assert "$openminion-state-ok:" in text
    assert LIGHT.chat_user_bg in text
    assert LIGHT.state_ok in text


def test_preamble_declares_one_variable_per_color_field() -> None:
    text = as_tcss_preamble(DARK)
    declaration_count = text.count("$openminion-")
    expected_count = len(DARK.color_field_names())
    assert declaration_count == expected_count, (
        f"preamble declared {declaration_count} variables; "
        f"catalog has {expected_count} hex-color fields"
    )


def test_preamble_is_safe_to_prepend() -> None:
    text = as_tcss_preamble(DARK)
    body_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("/*")
    ]
    for line in body_lines:
        assert line.startswith("$openminion-"), (
            f"non-declaration leaked into preamble: {line!r}"
        )
        assert line.endswith(";"), f"missing terminator: {line!r}"


@pytest.mark.asyncio
async def test_dashboard_mounts_with_theme_preamble_injected() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.active_theme.name == "dark"
        variables = app.stylesheet._variables
        assert "openminion-chat-user-bg" in variables, (
            f"theme preamble variables missing; got keys: "
            f"{sorted(variables.keys())[:10]}"
        )
        assert variables["openminion-chat-user-bg"] == DARK.chat_user_bg


@pytest.mark.asyncio
async def test_focus_mounts_with_theme_preamble_injected(tmp_path) -> None:
    working_dir = str(tmp_path)
    app = FocusApp(
        runtime=_DemoFocusRuntime(working_dir=working_dir), working_dir=working_dir
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.active_theme.name == "dark"
        variables = app.stylesheet._variables
        assert "openminion-chat-user-bg" in variables
        assert variables["openminion-chat-user-bg"] == DARK.chat_user_bg


@pytest.mark.asyncio
async def test_dashboard_apply_theme_updates_active_theme() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.active_theme.name == "dark"

        ok = app.apply_theme(LIGHT)
        await pilot.pause()
        assert ok is True
        assert app.active_theme.name == "light"
        variables = app.stylesheet._variables
        assert variables["openminion-chat-user-bg"] == LIGHT.chat_user_bg
        assert LIGHT.chat_user_bg != DARK.chat_user_bg


@pytest.mark.asyncio
async def test_focus_apply_theme_updates_active_theme(tmp_path) -> None:
    working_dir = str(tmp_path)
    app = FocusApp(
        runtime=_DemoFocusRuntime(working_dir=working_dir), working_dir=working_dir
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        ok = app.apply_theme(LIGHT)
        await pilot.pause()
        assert ok is True
        assert app.active_theme.name == "light"


@pytest.mark.asyncio
async def test_apply_theme_returns_false_on_bounded_failure() -> None:
    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        before = app.active_theme

        broken = object()
        ok = app.apply_theme(broken)  # type: ignore[arg-type]
        await pilot.pause()
        assert ok is False
        assert app.active_theme is before, (
            "bounded failure must leave the previous theme intact"
        )
