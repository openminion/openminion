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


def test_active_turn_footer_stays_identity_only() -> None:
    line = TerminalStatusLine()
    line.set_state(
        agent="alpha",
        model="x",
        cwd="/tmp/wd",
        state="responding",
        elapsed_seconds=2.5,
    )
    text = line.bottom_toolbar()
    assert "responding" not in text
    assert "2.5s" not in text
    assert "Esc cancel" not in text
    assert "alpha" in text
    assert "model: x" in text
    assert "cwd: /tmp/wd" in text


def test_active_turn_footer_suppresses_custom_status_copy() -> None:
    line = TerminalStatusLine()
    line.set_state(
        agent="minimax-m2-7",
        model="openai/MiniMax-M2.7",
        state="responding",
        elapsed_seconds=2.5,
        custom="Analyzing request...",
    )
    text = line.bottom_toolbar()
    assert "minimax-m2-7" in text
    assert "openai/MiniMax-M2.7" in text
    assert "Analyzing request..." not in text


def test_live_turn_footer_keeps_identity_without_active_timer_or_hint() -> None:
    line = TerminalStatusLine()
    line.set_state(
        agent="minimax-m2-7",
        model="openai/MiniMax-M2.7",
        cwd="/repo/openminion",
        tokens="1200/8000",
        state="responding",
        elapsed_seconds=6.8,
        custom="Loading session history...",
        queued_count=2,
    )
    text = line.live_turn_footer()
    assert "minimax-m2-7" in text
    assert "openai/MiniMax-M2.7" in text
    assert "/repo/openminion" in text
    assert "1200/8000" in text
    assert "queued: 2" in text
    assert "6.8s" not in text
    assert "Esc cancel" not in text
    assert "responding" not in text
    assert "Loading session history..." not in text


def test_tool_state_footer_stays_identity_only() -> None:
    line = TerminalStatusLine()
    line.set_state(
        state="tool",
        tool_name="bash",
        elapsed_seconds=0.1,
        agent="alpha",
        model="openai/test",
    )
    text = line.bottom_toolbar()
    assert "bash" not in text
    assert "0.1s" not in text
    assert "alpha" in text
    assert "openai/test" in text


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


def test_idle_toolbar_shows_queued_count_when_present() -> None:
    line = TerminalStatusLine()
    line.set_state(agent="alpha", queued_count=1)

    text = line.bottom_toolbar()

    assert "alpha" in text
    assert "queued: 1" in text
