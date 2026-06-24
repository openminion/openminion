from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

from rich.console import Console

from openminion.cli.tui.terminal.shell import _SLASH_COMMANDS, _handle_slash


def _dispatch(text: str) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    asyncio.run(
        _handle_slash(
            text,
            runtime=SimpleNamespace(),
            console=console,
            transcript=MagicMock(),
            overlay=MagicMock(),
            status_line=MagicMock(),
            working_dir="/tmp",
        )
    )
    return buf.getvalue()


def test_theme_in_slash_catalog() -> None:
    assert "/theme" in _SLASH_COMMANDS


def test_bare_theme_shows_active_and_available() -> None:
    out = _dispatch("/theme")
    assert "active:" in out
    assert "variant:" in out
    assert "available:" in out
    assert "dark" in out.lower()
    assert "light" in out.lower()


def test_bare_theme_shows_switch_hint() -> None:
    out = _dispatch("/theme")
    assert "Switch with" in out
    assert "balanced" in out
    assert "high_contrast" in out


def test_theme_switch_to_light_succeeds() -> None:
    from openminion.cli.presentation.styles import (
        get_active_theme_name,
        set_active_theme,
    )
    from openminion.cli.theme import DARK

    initial = get_active_theme_name()
    try:
        out = _dispatch("/theme light")
        assert "switched to light" in out
        assert get_active_theme_name() == "light"
    finally:
        if initial == "dark":
            set_active_theme(DARK)


def test_theme_switch_to_dark_succeeds() -> None:
    from openminion.cli.presentation.styles import (
        get_active_theme_name,
        set_active_theme,
    )
    from openminion.cli.theme import DARK, LIGHT

    initial = get_active_theme_name()
    try:
        # First set to light, then switch back to dark.
        set_active_theme(LIGHT)
        out = _dispatch("/theme dark")
        assert "switched to dark" in out
        assert get_active_theme_name() == "dark"
    finally:
        if initial == "dark":
            set_active_theme(DARK)
        else:
            set_active_theme(LIGHT)


def test_theme_switch_unknown_theme_surfaces_error() -> None:
    out = _dispatch("/theme neon")
    assert "unknown theme" in out
    assert "neon" in out
    assert "light" in out
    assert "dark" in out


def test_theme_switch_is_case_insensitive() -> None:
    from openminion.cli.presentation.styles import set_active_theme
    from openminion.cli.theme import DARK

    try:
        out = _dispatch("/theme LIGHT")
        assert "switched to light" in out
    finally:
        set_active_theme(DARK)


def test_theme_variant_switch_to_balanced() -> None:
    out = _dispatch("/theme variant balanced")
    assert "variant" in out.lower()
    assert "balanced" in out


def test_theme_variant_switch_to_high_contrast() -> None:
    out = _dispatch("/theme variant high_contrast")
    assert "high_contrast" in out


def test_theme_variant_unknown_surfaces_error() -> None:
    out = _dispatch("/theme variant neon")
    assert "unknown variant" in out
    assert "neon" in out


def test_theme_variant_missing_arg_surfaces_error() -> None:
    out = _dispatch("/theme variant")
    assert "unknown variant" in out
