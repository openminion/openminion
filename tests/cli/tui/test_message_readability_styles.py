from __future__ import annotations

from pathlib import Path


_STYLES = Path(
    Path(__file__).resolve().parents[3]
    / "src"
    / "openminion"
    / "cli"
    / "tui"
    / "styles.tcss"
).read_text(encoding="utf-8")


def test_agent_rows_use_theme_agent_tokens_for_readability() -> None:
    assert ".msg-agent {" in _STYLES
    assert "$openminion-chat-agent-bg" in _STYLES
    assert "$openminion-chat-agent-fg" in _STYLES
    assert "$openminion-text-accent" in _STYLES


def test_user_rows_stay_distinct_from_agent_rows() -> None:
    assert ".msg-user .message-body {" in _STYLES
    assert "color: $text-muted;" in _STYLES
