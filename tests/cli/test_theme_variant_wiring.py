from __future__ import annotations

from openminion.cli.presentation import styles
from openminion.cli.presentation.styles import (
    StyleToken,
    _ANSI_CODES,
    _ansi_codes_from_theme,
    _hex_to_truecolor_ansi,
    set_active_theme,
)
from openminion.cli.theme import DARK, LIGHT


def test_hex_to_truecolor_produces_ansi_sgr_pair() -> None:
    open_, close = _hex_to_truecolor_ansi("#1a2b3c")
    assert open_ == "\033[38;2;26;43;60m"
    assert close == "\033[0m"

    open_, _ = _hex_to_truecolor_ansi("ffffff")
    assert open_ == "\033[38;2;255;255;255m"

    assert _hex_to_truecolor_ansi("not a color") == ("", "")
    assert _hex_to_truecolor_ansi("#abc") == ("", "")  # 3-char shorthand not supported


def test_module_load_codes_derive_from_dark_theme() -> None:
    expected = _ansi_codes_from_theme(DARK)
    for token in StyleToken:
        assert _ANSI_CODES[token] == expected[token], (
            f"{token.value} differs from DARK-derived expected"
        )


def test_set_active_theme_swaps_codes_in_place() -> None:
    original = dict(_ANSI_CODES)
    try:
        set_active_theme(LIGHT)
        for token in StyleToken:
            field = styles._TOKEN_TO_THEME_FIELD[token]
            expected_open, _ = _hex_to_truecolor_ansi(getattr(LIGHT, field))
            assert _ANSI_CODES[token][0] == expected_open, (
                f"{token.value} did not swap to LIGHT after set_active_theme"
            )
        light_user = _ANSI_CODES[StyleToken.USER][0]
        set_active_theme(DARK)
        dark_user = _ANSI_CODES[StyleToken.USER][0]
        assert light_user != dark_user, (
            "USER token color must differ between LIGHT and DARK themes"
        )
    finally:
        _ANSI_CODES.clear()
        _ANSI_CODES.update(original)


def test_style_output_changes_after_theme_swap() -> None:
    from openminion.cli.presentation.styles import is_color_enabled, style

    if not is_color_enabled():
        return

    original = dict(_ANSI_CODES)
    try:
        set_active_theme(LIGHT)
        light_out = style(StyleToken.USER, "test")
        set_active_theme(DARK)
        dark_out = style(StyleToken.USER, "test")
        assert light_out != dark_out, (
            f"style() output should differ across themes; "
            f"light={light_out!r} dark={dark_out!r}"
        )
        assert "test" in light_out
        assert "test" in dark_out
    finally:
        _ANSI_CODES.clear()
        _ANSI_CODES.update(original)


def test_token_to_theme_field_covers_all_style_tokens() -> None:
    for token in StyleToken:
        assert token in styles._TOKEN_TO_THEME_FIELD, (
            f"{token.value} missing from _TOKEN_TO_THEME_FIELD; "
            f"theme-derived rebuild would silently drop it"
        )


def test_theme_field_names_in_mapping_exist_on_theme() -> None:
    theme_fields = set(DARK.color_field_names())
    for token, field in styles._TOKEN_TO_THEME_FIELD.items():
        assert field in theme_fields, (
            f"{token.value} maps to {field!r}, which is not a Theme field"
        )
