from __future__ import annotations

from unittest.mock import patch

from openminion.cli.presentation.styles import StyleToken
from openminion.cli.presentation.markers import (
    MARKER_ASSISTANT,
    MARKER_FAIL_SUFFIX,
    MARKER_TOOL_FAIL,
    MARKER_TOOL_OK,
    MARKER_TOOL_RUNNING,
    MARKER_USER,
    marker_ansi,
    marker_text,
)


def test_marker_glyph_token_pairs() -> None:
    expected_pairs = [
        (MARKER_ASSISTANT, "⏺", StyleToken.ASSISTANT),
        (MARKER_TOOL_RUNNING, "●", StyleToken.WARNING),
        (MARKER_TOOL_OK, "●", StyleToken.SUCCESS),
        (MARKER_TOOL_FAIL, "●", StyleToken.ERROR),
        (MARKER_USER, "◆", StyleToken.USER),
        (MARKER_FAIL_SUFFIX, "✗", StyleToken.ERROR),
    ]
    for marker, expected_glyph, expected_token in expected_pairs:
        glyph, token = marker
        assert glyph == expected_glyph
        assert token == expected_token


def test_marker_text_returns_rich_text_with_color() -> None:
    with patch(
        "openminion.cli.presentation.markers.is_color_enabled",
        return_value=True,
    ):
        text = marker_text(MARKER_TOOL_RUNNING)
    assert str(text) == "●"
    assert "yellow" in str(text.style)


def test_marker_text_bold_modifier_opt_in() -> None:
    with patch(
        "openminion.cli.presentation.markers.is_color_enabled",
        return_value=True,
    ):
        plain = marker_text(MARKER_TOOL_RUNNING, bold=False)
        bold = marker_text(MARKER_TOOL_RUNNING, bold=True)
    assert "bold" not in str(plain.style)
    assert "bold" in str(bold.style)


def test_marker_text_strips_color_when_disabled() -> None:
    with patch(
        "openminion.cli.presentation.markers.is_color_enabled",
        return_value=False,
    ):
        text = marker_text(MARKER_TOOL_RUNNING)
    assert str(text) == "●"
    assert "yellow" not in str(text.style)
    assert "red" not in str(text.style)


def test_marker_text_disabled_color_preserves_bold() -> None:
    with patch(
        "openminion.cli.presentation.markers.is_color_enabled",
        return_value=False,
    ):
        text = marker_text(MARKER_TOOL_RUNNING, bold=True)
    assert str(text.style) == "bold"


def test_marker_ansi_wraps_with_escapes_when_color_enabled() -> None:
    with (
        patch(
            "openminion.cli.presentation.markers.is_color_enabled",
            return_value=True,
        ),
        patch(
            "openminion.cli.presentation.styles.is_color_enabled",
            return_value=True,
        ),
    ):
        s = marker_ansi(MARKER_TOOL_RUNNING)
    assert "●" in s
    assert "\033[" in s


def test_marker_ansi_returns_bare_glyph_when_color_disabled() -> None:
    with patch(
        "openminion.cli.presentation.markers.is_color_enabled",
        return_value=False,
    ):
        s = marker_ansi(MARKER_TOOL_RUNNING)
    assert s == "●"


def test_all_markers_use_valid_style_tokens() -> None:
    markers = [
        MARKER_ASSISTANT,
        MARKER_TOOL_RUNNING,
        MARKER_TOOL_OK,
        MARKER_TOOL_FAIL,
        MARKER_USER,
        MARKER_FAIL_SUFFIX,
    ]
    for _, token in markers:
        assert isinstance(token, StyleToken)

