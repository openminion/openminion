from __future__ import annotations

import pytest
from rich.theme import Theme as RichTheme

from openminion.cli.theme import DARK, LIGHT
from openminion.cli.theme.rich_adapter import as_rich_theme


def test_returns_rich_theme_instance() -> None:
    result = as_rich_theme(LIGHT)
    assert isinstance(result, RichTheme)


def test_every_required_semantic_name_is_mapped() -> None:
    rich_theme = as_rich_theme(LIGHT)
    names = set(rich_theme.styles.keys())
    required = {
        "chat.user",
        "chat.agent",
        "chat.system",
        "chat.tool",
        "chat.error",
        "chat.user.fg",
        "chat.agent.fg",
        "chat.system.fg",
        "chat.tool.fg",
        "chat.error.fg",
        "surface.app",
        "surface.panel",
        "surface.divider",
        "text.primary",
        "text.secondary",
        "text.muted",
        "text.accent",
        "state.ok",
        "state.warning",
        "state.error",
        "state.offline",
        "state.highlight",
    }
    missing = required - names
    assert not missing, f"adapter missing semantic names: {sorted(missing)}"


@pytest.mark.parametrize("theme", [LIGHT, DARK])
def test_chat_kind_pairs_include_both_fg_and_bg(theme) -> None:
    rich_theme = as_rich_theme(theme)
    for kind in ("chat.user", "chat.agent", "chat.system", "chat.tool", "chat.error"):
        style = rich_theme.styles[kind]
        assert style.color is not None, f"{kind} has no foreground color"
        assert style.bgcolor is not None, f"{kind} has no background color"


def test_light_and_dark_produce_distinguishable_user_styles() -> None:
    light_theme = as_rich_theme(LIGHT)
    dark_theme = as_rich_theme(DARK)
    assert str(light_theme.styles["chat.user"]) != str(
        dark_theme.styles["chat.user"]
    ), "LIGHT and DARK must not collapse to the same Rich style"


def test_colors_resolve_to_their_theme_field_values() -> None:
    rich_theme = as_rich_theme(LIGHT)
    user_style = rich_theme.styles["chat.user"]
    assert LIGHT.chat_user_fg.lstrip("#") in str(user_style).lower()
    assert LIGHT.chat_user_bg.lstrip("#") in str(user_style).lower()


def test_adapter_is_pure_no_side_effects() -> None:
    a = as_rich_theme(LIGHT)
    b = as_rich_theme(LIGHT)
    assert set(a.styles.keys()) == set(b.styles.keys())
    for name in a.styles:
        assert str(a.styles[name]) == str(b.styles[name])
