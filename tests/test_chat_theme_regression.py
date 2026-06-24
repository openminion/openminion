import sys
from io import StringIO

import pytest

from openminion.cli.presentation.styles import StyleToken, _ANSI_CODES, format_prefix


def test_style_tokens_all_defined() -> None:
    for token in StyleToken:
        assert token in _ANSI_CODES, f"Token {token} missing from _ANSI_CODES"


def test_theme_info_includes_version() -> None:
    from openminion.cli.presentation.styles import get_theme_info

    info = get_theme_info()
    assert "theme_version" in info
    assert info["theme_version"] == "2.0"


def test_theme_info_includes_variant() -> None:
    from openminion.cli.presentation.styles import get_theme_info

    info = get_theme_info()
    assert "theme_variant" in info
    assert info["theme_variant"] in ["balanced", "high_contrast"]


def test_theme_variant_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from openminion.cli.presentation import styles

    monkeypatch.setenv("OPENMINION_THEME_VARIANT", "high_contrast")
    styles._THEME_VARIANT = None
    assert styles._get_theme_variant() == "high_contrast"


def test_invalid_theme_variant_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from openminion.cli.presentation import styles

    monkeypatch.setenv("OPENMINION_THEME_VARIANT", "invalid_variant")
    styles._THEME_VARIANT = None
    assert styles._get_theme_variant() == "balanced"


def test_style_output_format() -> None:
    from openminion.cli.presentation.styles import is_color_enabled, style

    result = style(StyleToken.USER, "test")
    assert "test" in result
    if is_color_enabled():
        assert "\033[" in result
    else:
        assert result == "test"


def test_format_prefix_format() -> None:
    result = format_prefix(StyleToken.USER, "You")
    assert result.startswith("You")
    assert result.endswith(": ")


def test_clear_line_format() -> None:
    from openminion.cli.presentation.styles import clear_line, is_color_enabled

    result = clear_line()
    assert "\r" in result
    if is_color_enabled():
        assert "\033[K" in result


def test_spinner_frame_generation() -> None:
    from openminion.cli.presentation.styles import get_spinner_frame, reset_spinner

    reset_spinner()
    frames_seen = {get_spinner_frame() for _ in range(15)}
    assert len(frames_seen) > 1, "Spinner should produce different frames"


def test_color_mode_detection_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    from openminion.cli.presentation import styles

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("OPENMINION_COLOR", "1")
    styles._COLOR_MODE = None
    assert styles.get_color_mode() == "off"


def test_non_tty_disables_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    from openminion.cli.presentation import styles

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("OPENMINION_COLOR", raising=False)
    old_stdout = sys.stdout
    try:
        sys.stdout = StringIO()
        styles._COLOR_MODE = None
        assert styles.get_color_mode() == "off"
    finally:
        sys.stdout = old_stdout
