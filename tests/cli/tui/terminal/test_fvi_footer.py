from __future__ import annotations

from openminion.cli.tui.terminal.status_line import TerminalStatusLine


def test_idle_footer_contains_agent_model_cwd() -> None:
    sl = TerminalStatusLine()
    sl.set_state(
        state="idle",
        agent="test-agent",
        model="openai/gpt-4",
        cwd="/my/dir",
    )
    out = sl.bottom_toolbar()
    assert "test-agent" in out
    assert "gpt-4" in out
    assert "/my/dir" in out


def test_idle_footer_includes_usage_summary() -> None:
    sl = TerminalStatusLine()
    sl.set_state(
        state="idle",
        agent="a",
        model="m",
        cwd="/c",
        usage_summary="1.2k tokens · $0.005",
    )
    out = sl.bottom_toolbar()
    assert "1.2k tokens" in out


# ── Keybind reminder strings are GONE ────────────────────────────


def test_idle_footer_does_not_contain_enter_to_send() -> None:
    sl = TerminalStatusLine()
    sl.set_state(state="idle", agent="a", model="m", cwd="/c")
    out = sl.bottom_toolbar()
    assert "Enter to send" not in out


def test_idle_footer_does_not_contain_shift_enter() -> None:
    sl = TerminalStatusLine()
    sl.set_state(state="idle", agent="a", model="m", cwd="/c")
    out = sl.bottom_toolbar()
    assert "Shift+Enter" not in out


def test_idle_footer_does_not_contain_esc_to_clear() -> None:
    sl = TerminalStatusLine()
    sl.set_state(state="idle", agent="a", model="m", cwd="/c")
    out = sl.bottom_toolbar()
    assert "Esc to clear" not in out


def test_idle_footer_does_not_contain_ctrl_j_newline() -> None:
    sl = TerminalStatusLine()
    sl.set_state(state="idle", agent="a", model="m", cwd="/c")
    out = sl.bottom_toolbar()
    assert "Ctrl+J newline" not in out


def test_idle_footer_does_not_contain_type_to_ask_hint() -> None:
    sl = TerminalStatusLine()
    sl.set_state(state="idle", agent="a", model="m", cwd="/c")
    out = sl.bottom_toolbar()
    assert "Type to ask" not in out


# ── Empty-state behavior ─────────────────────────────────────────


def test_idle_footer_with_no_state_is_empty() -> None:
    sl = TerminalStatusLine()
    sl.set_state(state="idle")
    out = sl.bottom_toolbar()
    assert out == ""


def test_idle_footer_with_partial_state() -> None:
    sl = TerminalStatusLine()
    sl.set_state(state="idle", agent="solo-agent")
    out = sl.bottom_toolbar()
    assert "solo-agent" in out
    # No keybind reminders.
    assert "Enter to send" not in out
    assert "Ctrl+J" not in out


# ── API regression guard ─────────────────────────────────────────


def test_set_state_api_unchanged() -> None:
    sl = TerminalStatusLine()
    # Same shape FTR-02 + FNS-06 + FIA-era usage push.
    sl.set_state(
        state="idle",
        agent="agent-id",
        model="provider/model",
        cwd="/cwd",
        elapsed_seconds=0.0,
        usage_summary="1k tokens",
    )
    out = sl.bottom_toolbar()
    assert "agent-id" in out
    assert "1k tokens" in out


# ── Active turn states stay out of the footer ───────────────────


def test_responding_state_does_not_reenter_footer() -> None:
    sl = TerminalStatusLine()
    sl.set_state(
        state="responding",
        elapsed_seconds=1.5,
        agent="alpha",
        model="openai/test",
    )
    out = sl.bottom_toolbar()
    assert "responding" not in out
    assert "1.5s" not in out
    assert "Esc cancel" not in out
    assert "alpha" in out


def test_tool_state_does_not_reenter_footer() -> None:
    sl = TerminalStatusLine()
    sl.set_state(
        state="tool",
        tool_name="Bash",
        elapsed_seconds=2.0,
        agent="alpha",
        model="openai/test",
    )
    out = sl.bottom_toolbar()
    assert "Bash" not in out
    assert "2.0s" not in out
    assert "Esc cancel" not in out
    assert "alpha" in out
