from __future__ import annotations

import re
from dataclasses import FrozenInstanceError, fields

import pytest

from openminion.cli.theme import DARK, LIGHT, SHIPPED_THEMES, Theme

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_theme_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        LIGHT.name = "mutated"  # type: ignore[misc]


def test_theme_field_set_matches_spec_categories() -> None:
    field_names = {f.name for f in fields(Theme)}
    expected = {
        "name",
        "chat_user_bg",
        "chat_user_fg",
        "chat_agent_bg",
        "chat_agent_fg",
        "chat_system_bg",
        "chat_system_fg",
        "chat_tool_bg",
        "chat_tool_fg",
        "chat_error_bg",
        "chat_error_fg",
        "surface_app_bg",
        "surface_panel_bg",
        "surface_divider",
        "text_primary",
        "text_secondary",
        "text_muted",
        "text_accent",
        "state_ok",
        "state_warning",
        "state_error",
        "state_offline",
        "state_highlight",
    }
    assert field_names == expected, (
        f"Theme field set drifted from spec §5.1.\n"
        f"  missing: {expected - field_names}\n"
        f"  extra:   {field_names - expected}"
    )


def test_color_field_names_excludes_name() -> None:
    color_names = LIGHT.color_field_names()
    assert "name" not in color_names
    assert len(color_names) == 22, len(color_names)


def test_color_pairs_returns_chat_kind_fg_bg_pairs() -> None:
    pairs = LIGHT.color_pairs()
    pair_names = [p[0] for p in pairs]
    assert pair_names == [
        "chat_user",
        "chat_agent",
        "chat_system",
        "chat_tool",
        "chat_error",
    ]
    for name, fg, bg in pairs:
        assert _HEX_COLOR.match(fg), f"{name}: fg {fg!r} is not a valid #RRGGBB hex"
        assert _HEX_COLOR.match(bg), f"{name}: bg {bg!r} is not a valid #RRGGBB hex"


@pytest.mark.parametrize("theme,expected_name", [(LIGHT, "light"), (DARK, "dark")])
def test_shipped_theme_has_correct_name(theme: Theme, expected_name: str) -> None:
    assert theme.name == expected_name


@pytest.mark.parametrize("theme", [LIGHT, DARK])
def test_every_shipped_color_is_valid_hex(theme: Theme) -> None:
    for field_name in theme.color_field_names():
        value = getattr(theme, field_name)
        assert _HEX_COLOR.match(value), (
            f"{theme.name}.{field_name} = {value!r} is not a valid #RRGGBB hex"
        )


def test_shipped_themes_mapping_indexes_by_name() -> None:
    assert set(SHIPPED_THEMES.keys()) == {"light", "dark"}
    assert SHIPPED_THEMES["light"] is LIGHT
    assert SHIPPED_THEMES["dark"] is DARK


def test_light_and_dark_use_distinct_palettes() -> None:
    light_colors = {n: getattr(LIGHT, n) for n in LIGHT.color_field_names()}
    dark_colors = {n: getattr(DARK, n) for n in DARK.color_field_names()}
    assert light_colors != dark_colors, "LIGHT and DARK must define different colors"
    assert LIGHT.surface_app_bg != DARK.surface_app_bg


def test_theme_instances_are_hashable_for_adapter_memoisation() -> None:
    cache: dict[Theme, str] = {LIGHT: "light-derived", DARK: "dark-derived"}
    assert cache[LIGHT] == "light-derived"
    assert cache[DARK] == "dark-derived"
