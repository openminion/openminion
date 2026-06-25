from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.cli.tui.focus.widgets.status_line import (
    FocusStatusLine,
    TOKENS_SEVERITY_DANGER,
    TOKENS_SEVERITY_NORMAL,
    TOKENS_SEVERITY_WARN,
    classify_context_severity,
)


# ── classify_context_severity ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "used,limit,expected",
    [
        (0, 200_000, TOKENS_SEVERITY_NORMAL),
        (140_000, 200_000, TOKENS_SEVERITY_NORMAL),
        (160_000, 200_000, TOKENS_SEVERITY_WARN),
        (170_000, 200_000, TOKENS_SEVERITY_WARN),
        (190_000, 200_000, TOKENS_SEVERITY_WARN),
        (191_000, 200_000, TOKENS_SEVERITY_DANGER),
        (199_000, 200_000, TOKENS_SEVERITY_DANGER),
    ],
)
def test_classify_severity_thresholds(used: int, limit: int, expected: str) -> None:
    assert classify_context_severity(used, limit) == expected


def test_classify_severity_missing_limit_falls_back_to_normal() -> None:
    assert classify_context_severity(100_000, None) == TOKENS_SEVERITY_NORMAL
    assert classify_context_severity(100_000, 0) == TOKENS_SEVERITY_NORMAL


def test_classify_severity_invalid_input_falls_back_to_normal() -> None:
    assert classify_context_severity("not a number", 200_000) == (
        TOKENS_SEVERITY_NORMAL
    )
    assert classify_context_severity(100, "not a limit") == (TOKENS_SEVERITY_NORMAL)


# ── Status-line tokens segment composition ───────────────────────────────────


def test_tokens_segment_normal_no_glyph() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="idle",
        tokens="140000/200000",
        tokens_severity=TOKENS_SEVERITY_NORMAL,
    )
    text = line._text()
    assert "tokens: 140000/200000" in text
    assert "⚠" not in text
    assert "⛔" not in text


def test_tokens_segment_warn_includes_warn_glyph() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="idle",
        tokens="170000/200000",
        tokens_severity=TOKENS_SEVERITY_WARN,
    )
    text = line._text()
    assert "tokens: 170000/200000" in text
    assert "⚠" in text
    assert "⛔" not in text


def test_tokens_segment_danger_includes_danger_glyph() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="idle",
        tokens="195000/200000",
        tokens_severity=TOKENS_SEVERITY_DANGER,
    )
    text = line._text()
    assert "tokens: 195000/200000" in text
    assert "⛔" in text


def test_tokens_segment_unknown_severity_clamps_to_normal() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="idle",
        tokens="100/200",
        tokens_severity="weird-mode",
    )
    text = line._text()
    assert line.tokens_severity == TOKENS_SEVERITY_NORMAL
    assert "⚠" not in text
    assert "⛔" not in text


# ── Active-turn state preserves warning richness ─────────────────────────────


def test_responding_state_preserves_warning_glyph() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="responding",
        elapsed_seconds=4.0,
        tokens="195000/200000",
        tokens_severity=TOKENS_SEVERITY_DANGER,
    )
    text = line._text()
    assert "responding" in text
    assert "tokens:" in text
    assert "⛔" in text


def test_tool_state_preserves_warning_glyph() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="tool",
        tool_name="exec.run",
        elapsed_seconds=1.0,
        tokens="195000/200000",
        tokens_severity=TOKENS_SEVERITY_DANGER,
    )
    text = line._text()
    assert "exec.run" in text
    assert "tokens:" in text
    assert "⛔" in text


# ── FocusScreen._tokens_severity helper ──────────────────────────────────────


def test_screen_helper_returns_normal_when_snapshot_getter_missing() -> None:
    from openminion.cli.tui.focus.screen import FocusScreen

    assert FocusScreen._tokens_severity(None) == TOKENS_SEVERITY_NORMAL


def test_screen_helper_returns_normal_when_snapshot_returns_none() -> None:
    from openminion.cli.tui.focus.screen import FocusScreen

    assert FocusScreen._tokens_severity(lambda: None) == TOKENS_SEVERITY_NORMAL


def test_screen_helper_classifies_from_real_snapshot() -> None:
    from openminion.cli.status.token_usage import TokenUsageSnapshot
    from openminion.cli.tui.focus.screen import FocusScreen

    snap = TokenUsageSnapshot(
        context_used_tokens=170_000,
        context_limit_tokens=200_000,
    )
    assert FocusScreen._tokens_severity(lambda: snap) == TOKENS_SEVERITY_WARN
    snap_danger = TokenUsageSnapshot(
        context_used_tokens=199_000,
        context_limit_tokens=200_000,
    )
    assert FocusScreen._tokens_severity(lambda: snap_danger) == TOKENS_SEVERITY_DANGER


def test_screen_helper_returns_normal_when_limit_absent() -> None:
    from openminion.cli.status.token_usage import TokenUsageSnapshot
    from openminion.cli.tui.focus.screen import FocusScreen

    snap = TokenUsageSnapshot(context_used_tokens=999_999)
    assert FocusScreen._tokens_severity(lambda: snap) == TOKENS_SEVERITY_NORMAL


def test_screen_helper_handles_snapshot_exception() -> None:
    from openminion.cli.tui.focus.screen import FocusScreen

    def _boom():
        raise RuntimeError("snapshot exploded")

    assert FocusScreen._tokens_severity(_boom) == TOKENS_SEVERITY_NORMAL


def test_statusline_custom_label_handles_expected_getter_failures() -> None:
    from openminion.cli.tui.focus.status import FocusLabelsMixin

    class _Probe(FocusLabelsMixin):
        def __init__(self) -> None:
            self._runtime = SimpleNamespace(
                statusline_label=lambda: (_ for _ in ()).throw(TypeError("bad label"))
            )

    assert _Probe()._statusline_custom_label() == ""
