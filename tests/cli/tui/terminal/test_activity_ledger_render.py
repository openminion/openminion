from __future__ import annotations

import io

from rich.console import Console

from openminion.cli.status.activity_ledger import (
    KIND_APPROVAL,
    KIND_BACKGROUND,
    KIND_BUDGET,
    KIND_ERROR,
    KIND_PLAN,
    KIND_TOOL,
    STATE_BLOCKED,
    STATE_COMPLETED,
    STATE_DENIED,
    STATE_FAILED,
    STATE_RUNNING,
    TurnActivityEvent,
)
from openminion.cli.tui.terminal.transcript import TerminalTranscript


def _make_transcript(
    verbosity: str = "normal",
) -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, no_color=True)
    return TerminalTranscript(console, verbosity=verbosity), buf


def test_push_activity_event_renders_plan_step_done() -> None:
    t, buf = _make_transcript()
    t.push_activity_event(
        TurnActivityEvent(kind=KIND_PLAN, state=STATE_COMPLETED, title="ship")
    )
    out = buf.getvalue()
    assert "Plan step done: ship" in out


def test_push_activity_event_renders_plan_step_blocked_with_reason() -> None:
    t, buf = _make_transcript()
    t.push_activity_event(
        TurnActivityEvent(
            kind=KIND_PLAN,
            state=STATE_BLOCKED,
            title="deploy",
            detail="missing token",
        )
    )
    out = buf.getvalue()
    assert "Plan step blocked: deploy — missing token" in out


def test_push_activity_event_renders_full_plan_via_render_plan() -> None:
    t, buf = _make_transcript()
    t.push_activity_event(
        TurnActivityEvent(
            kind=KIND_PLAN,
            state=STATE_RUNNING,
            plan={
                "summary": "Smoke",
                "items": [
                    {"text": "lint", "status": "done"},
                    {"text": "test", "status": "todo"},
                ],
            },
        )
    )
    out = buf.getvalue()
    assert "Plan" in out
    assert "lint" in out and "test" in out


def test_push_activity_event_renders_approval_states() -> None:
    t_req, buf_req = _make_transcript()
    t_req.push_activity_event(
        TurnActivityEvent(
            kind=KIND_APPROVAL, state=STATE_RUNNING, tool_name="git.reset"
        )
    )
    assert "Approval requested: git.reset" in buf_req.getvalue()

    t_d, buf_d = _make_transcript()
    t_d.push_activity_event(
        TurnActivityEvent(
            kind=KIND_APPROVAL,
            state=STATE_DENIED,
            tool_name="git.reset",
            detail="outside workspace",
        )
    )
    assert "Approval denied: git.reset — outside workspace" in buf_d.getvalue()

    t_a, buf_a = _make_transcript()
    t_a.push_activity_event(
        TurnActivityEvent(
            kind=KIND_APPROVAL, state=STATE_COMPLETED, tool_name="git.reset"
        )
    )
    assert "Approval allowed: git.reset" in buf_a.getvalue()


def test_push_activity_event_renders_background_running_and_done() -> None:
    t_r, buf_r = _make_transcript()
    t_r.push_activity_event(
        TurnActivityEvent(kind=KIND_BACKGROUND, state=STATE_RUNNING, title="research")
    )
    assert "Background: research" in buf_r.getvalue()

    t_d, buf_d = _make_transcript()
    t_d.push_activity_event(
        TurnActivityEvent(
            kind=KIND_BACKGROUND,
            state=STATE_COMPLETED,
            title="research",
            duration_ms=7000,
        )
    )
    assert "Background done: research (7000 ms)" in buf_d.getvalue()


def test_push_activity_event_renders_budget_event() -> None:
    t, buf = _make_transcript()
    t.push_activity_event(TurnActivityEvent(kind=KIND_BUDGET, title="tokens_low"))
    assert "Budget: tokens_low" in buf.getvalue()


def test_push_activity_event_uses_active_turn_render_path() -> None:
    t, buf = _make_transcript()
    handle = t.begin_turn()
    t.push_activity_event(TurnActivityEvent(kind=KIND_BUDGET, title="budget.allocated"))
    handle.complete(final_text="done")

    out = buf.getvalue()
    assert "Budget: allocated" in out
    assert "Budget event" not in out


def test_push_activity_event_renders_error() -> None:
    t, buf = _make_transcript()
    t.push_activity_event(
        TurnActivityEvent(
            kind=KIND_ERROR,
            state=STATE_FAILED,
            title="RuntimeError",
            detail="boom",
        )
    )
    assert "Error: RuntimeError — boom" in buf.getvalue()


def test_push_activity_event_skips_tool_events_to_preserve_fle() -> None:
    t, buf = _make_transcript()
    t.push_activity_event(
        TurnActivityEvent(
            kind=KIND_TOOL,
            state=STATE_RUNNING,
            tool_name="bash",
            args={"command": "ls"},
        )
    )
    assert buf.getvalue() == ""


def test_push_activity_event_skips_empty_events() -> None:
    t, buf = _make_transcript()
    t.push_activity_event(None)
    t.push_activity_event(TurnActivityEvent(kind="status", title=""))
    assert buf.getvalue() == ""


def test_push_activity_event_does_not_break_existing_tool_lifecycle() -> None:
    t, buf = _make_transcript()
    t.handle_tool_started(
        {"call_id": "c1", "tool_name": "bash", "args": {"command": "ls"}}
    )
    out = buf.getvalue()
    # Just confirm something was rendered (running marker / verb).
    assert "bash" in out.lower() or "ls" in out
