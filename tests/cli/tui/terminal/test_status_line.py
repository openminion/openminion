from __future__ import annotations

from openminion.cli.tui.terminal.status_line import TerminalStatusLine
from openminion.cli.tui.presentation.contracts import StatusLine


def test_status_line_satisfies_protocol() -> None:
    line = TerminalStatusLine()
    assert isinstance(line, StatusLine)


def test_idle_toolbar_chains_segments() -> None:
    line = TerminalStatusLine()
    line.set_state(
        agent="alpha",
        cwd="/tmp/wd",
        model="openai/gpt-4.1-mini",
        branch="main",
        tokens="123/8000",
        cost="$0.01",
    )
    text = line.bottom_toolbar()
    assert "alpha" in text
    assert "/tmp/wd" in text
    assert "openai/gpt-4.1-mini" in text
    assert "main" in text
    assert "123/8000" in text
    assert "$0.01" in text


def test_responding_state_overrides_idle_segments() -> None:
    line = TerminalStatusLine()
    line.set_state(model="x", state="responding", elapsed_seconds=2.5)
    text = line.bottom_toolbar()
    assert "responding" in text
    assert "2.5s" in text
    # Idle segments suppressed during responding.
    assert "model" not in text


def test_tool_state_shows_tool_name() -> None:
    line = TerminalStatusLine()
    line.set_state(state="tool", tool_name="bash", elapsed_seconds=0.1)
    text = line.bottom_toolbar()
    assert "bash" in text
    assert "0.1s" in text


def test_input_state_no_longer_appends_keybind_hint_suffix() -> None:
    line = TerminalStatusLine()
    line.set_state(input_state="typing")
    text = line.bottom_toolbar()
    # Pre-FVI-04: contained "Enter to send · ↑/↓ history · Ctrl+J newline"
    # Post-FVI-04: no keybind hints in footer.
    assert "Enter to send" not in text
    assert "Ctrl+J" not in text


def test_unknown_input_state_falls_back_to_empty() -> None:
    line = TerminalStatusLine()
    line.set_state(input_state="weird")
    # Falls back gracefully — toolbar still renders.
    assert isinstance(line.bottom_toolbar(), str)
