from __future__ import annotations

from unittest.mock import patch

from openminion.cli.tui.terminal.status_line import TerminalStatusLine


# ── Color enabled: segments wrapped ──────────────────────────────


def test_idle_segments_contain_ansi_when_color_enabled() -> None:
    line = TerminalStatusLine()
    line.set_state(agent="alpha", model="openai/test", cwd="/tmp")
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=True,
    ):
        text = line.bottom_toolbar()
    assert "\033[" in text
    # Content still present.
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


# ── Responding state ─────────────────────────────────────────────


def test_responding_state_wraps_marker_and_label() -> None:
    line = TerminalStatusLine()
    line.set_state(state="responding", elapsed_seconds=1.5)
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=True,
    ):
        text = line.bottom_toolbar()
    assert "●" in text
    assert "responding" in text
    assert "1.5s" in text
    assert "Esc cancel" in text
    assert "\033[" in text


def test_responding_state_plain_when_color_disabled() -> None:
    line = TerminalStatusLine()
    line.set_state(state="responding", elapsed_seconds=2.0)
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=False,
    ):
        text = line.bottom_toolbar()
    assert text == "● responding   2.0s   Esc cancel"


# ── Tool state ───────────────────────────────────────────────────


def test_tool_state_wraps_marker_and_tool_name() -> None:
    line = TerminalStatusLine()
    line.set_state(state="tool", tool_name="Bash", elapsed_seconds=0.5)
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=True,
    ):
        text = line.bottom_toolbar()
    assert "⚙" in text
    assert "Bash" in text
    assert "0.5s" in text
    assert "Esc cancel" in text
    assert "\033[" in text


def test_tool_state_falls_back_when_tool_name_unset() -> None:
    line = TerminalStatusLine()
    line.set_state(state="tool", elapsed_seconds=1.0)
    with patch(
        "openminion.cli.presentation.styles.is_color_enabled",
        return_value=False,
    ):
        text = line.bottom_toolbar()
    assert "⚙ tool" in text


# ── Token severity escalation ────────────────────────────────────


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
    # Token value renders with SOME color escape.
    assert "100/8000" in text
    assert "\033[" in text


def test_tokens_severity_warning_uses_warning_color() -> None:
    from openminion.cli.presentation.styles import StyleToken, style_token

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
    from openminion.cli.presentation.styles import StyleToken, style_token

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


# ── Empty state ──────────────────────────────────────────────────


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
