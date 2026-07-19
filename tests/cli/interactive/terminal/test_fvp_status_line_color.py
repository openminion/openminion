from __future__ import annotations

from unittest.mock import patch

from openminion.cli.presentation.styles import StyleToken, style_token
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine


def test_idle_segments_contain_ansi_when_color_enabled() -> None:
    line = TerminalStatusLine()
    line.set_state(agent="alpha", model="openai/test", cwd="/tmp")
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=True,
    ):
        text = line.bottom_toolbar()
    assert "\033[" in text
    assert "alpha" in text
    assert "openai/test" in text


def test_idle_segments_are_plain_when_color_disabled() -> None:
    line = TerminalStatusLine()
    line.set_state(agent="alpha", model="openai/test", cwd="/tmp")
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=False,
    ):
        text = line.bottom_toolbar()
    assert "\033[" not in text
    assert "alpha" in text
    assert "openai/test" in text


def test_active_turn_footer_keeps_identity_coloring_without_status_copy() -> None:
    line = TerminalStatusLine()
    line.set_state(
        state="responding",
        elapsed_seconds=1.5,
        agent="alpha",
        model="openai/test",
        cwd="/tmp",
    )
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=True,
    ):
        text = line.bottom_toolbar()
    assert "\033[" in text
    assert "alpha" in text
    assert "openai/test" in text
    assert "responding" not in text
    assert "1.5s" not in text
    assert "Esc cancel" not in text


def test_active_turn_footer_is_plain_when_color_disabled() -> None:
    line = TerminalStatusLine()
    line.set_state(
        state="responding",
        elapsed_seconds=2.0,
        agent="alpha",
        model="openai/test",
    )
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=False,
    ):
        text = line.bottom_toolbar()
    assert "\033[" not in text
    assert "alpha" in text
    assert "responding" not in text
    assert "2.0s" not in text


def test_active_turn_brain_row_uses_warning_color_in_live_footer() -> None:
    line = TerminalStatusLine()
    line.set_state(
        state="responding",
        elapsed_seconds=2.0,
        agent="alpha",
        model="openai/test",
        turn_status="Analyzing request...",
    )
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=True,
    ):
        text = line.live_turn_footer()
        warn_open, _ = style_token(StyleToken.WARNING)
    rows = text.splitlines()
    assert len(rows) == 2
    assert "brain:" in rows[0]
    assert "Analyzing request..." in rows[0]
    assert warn_open in rows[0]
    assert "queue:" not in rows[1]
    assert warn_open not in rows[1]


def test_active_turn_brain_row_uses_warning_color_in_bottom_toolbar() -> None:
    line = TerminalStatusLine()
    line.set_state(
        state="responding",
        elapsed_seconds=2.0,
        agent="alpha",
        model="openai/test",
        turn_status="Analyzing request...",
    )
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=True,
    ):
        text = line.bottom_toolbar()
        warn_open, _ = style_token(StyleToken.WARNING)
    rows = text.splitlines()
    assert len(rows) == 2
    assert "brain:" in rows[0]
    assert "Analyzing request..." in rows[0]
    assert warn_open in rows[0]
    assert "alpha" in rows[1]
    assert warn_open not in rows[1]


def test_live_turn_footer_keeps_ansi_identity_segments() -> None:
    line = TerminalStatusLine()
    line.set_state(
        state="tool",
        tool_name="Bash",
        elapsed_seconds=0.5,
        agent="alpha",
        model="openai/test",
        cwd="/tmp",
    )
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=True,
    ):
        text = line.live_turn_footer()
    assert "\033[" in text
    assert "alpha" in text
    assert "openai/test" in text
    assert "Bash" not in text
    assert "0.5s" not in text
    assert "Esc cancel" not in text


def test_live_turn_footer_omits_custom_status_label_even_when_set() -> None:
    line = TerminalStatusLine()
    line.set_state(
        state="responding",
        elapsed_seconds=1.0,
        agent="alpha",
        custom="Analyzing request...",
    )
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=False,
    ):
        text = line.live_turn_footer()
    assert "Analyzing request..." not in text


def test_tokens_severity_normal_uses_system_token() -> None:
    line = TerminalStatusLine()
    line.set_state(
        agent="alpha",
        tokens="100/8000",
    )
    line.tokens_severity = "normal"
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=True,
    ):
        text = line.bottom_toolbar()
    assert "100/8000" in text
    assert "\033[" in text


def test_tokens_severity_warning_uses_warning_color() -> None:
    line = TerminalStatusLine()
    line.set_state(agent="alpha", tokens="7800/8000")
    line.tokens_severity = "warning"
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=True,
    ):
        text = line.bottom_toolbar()
        warn_open, _ = style_token(StyleToken.WARNING)
    assert warn_open in text


def test_tokens_severity_error_uses_error_color() -> None:
    line = TerminalStatusLine()
    line.set_state(agent="alpha", tokens="8100/8000")
    line.tokens_severity = "error"
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=True,
    ):
        text = line.bottom_toolbar()
        err_open, _ = style_token(StyleToken.ERROR)
    assert err_open in text


def test_empty_state_returns_empty_string() -> None:
    line = TerminalStatusLine()
    text = line.bottom_toolbar()
    assert text == ""


def test_usage_summary_only_no_segments() -> None:
    line = TerminalStatusLine()
    line.set_state(usage_summary="42 tokens · $0.01")
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=False,
    ):
        text = line.bottom_toolbar()
    assert "42 tokens" in text


def test_usage_summary_appended_to_segments() -> None:
    line = TerminalStatusLine()
    line.set_state(agent="alpha", usage_summary="42t")
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=False,
    ):
        text = line.bottom_toolbar()
    assert "alpha" in text
    assert "42t" in text
