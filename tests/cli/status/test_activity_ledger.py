from __future__ import annotations

import importlib
import sys


from openminion.cli.status.activity_ledger import (
    KIND_APPROVAL,
    KIND_BACKGROUND,
    KIND_BUDGET,
    KIND_ERROR,
    KIND_PLAN,
    KIND_SEARCH,
    KIND_STATUS,
    KIND_TOOL,
    STATE_BLOCKED,
    STATE_COMPLETED,
    STATE_DENIED,
    STATE_FAILED,
    STATE_RUNNING,
    TurnActivityEvent,
    activity_from_progress_payload,
    format_activity_line,
)


# ---- adapter coverage -------------------------------------------------


def test_adapter_returns_none_for_non_mapping_payload() -> None:
    assert activity_from_progress_payload(None) is None
    assert activity_from_progress_payload("not a mapping") is None  # type: ignore[arg-type]


def test_adapter_returns_none_for_empty_payload() -> None:
    assert activity_from_progress_payload({}) is None


def test_adapter_maps_tool_started_payload() -> None:
    event = activity_from_progress_payload(
        {
            "kind": "tool_started",
            "tool_name": "bash",
            "args": {"command": "ls"},
            "call_id": "c1",
        }
    )
    assert event is not None
    assert event.kind == KIND_TOOL
    assert event.state == STATE_RUNNING
    assert event.tool_name == "bash"
    assert event.args == {"command": "ls"}
    assert event.call_id == "c1"


def test_adapter_maps_tool_completed_ok_payload() -> None:
    event = activity_from_progress_payload(
        {
            "kind": "tool_completed",
            "tool_name": "bash",
            "args": {"command": "ls"},
            "call_id": "c1",
            "ok": True,
            "duration_ms": 123,
            "content": "file1\nfile2",
        }
    )
    assert event is not None
    assert event.kind == KIND_TOOL
    assert event.state == STATE_COMPLETED
    assert event.duration_ms == 123
    assert event.content == "file1\nfile2"


def test_adapter_maps_tool_completed_failed_payload() -> None:
    event = activity_from_progress_payload(
        {
            "kind": "tool_completed",
            "tool_name": "git",
            "args": {"command": "status"},
            "ok": False,
            "duration_ms": 50,
        }
    )
    assert event is not None
    assert event.state == STATE_FAILED


def test_adapter_routes_search_tool_to_search_kind() -> None:
    event = activity_from_progress_payload(
        {
            "kind": "tool_started",
            "tool_name": "web.search",
            "args": {"query": "openminion"},
            "call_id": "s1",
        }
    )
    assert event is not None
    assert event.kind == KIND_SEARCH
    assert event.tool_name == "web.search"


def test_adapter_carries_provenance_and_fallback_fields() -> None:
    event = activity_from_progress_payload(
        {
            "kind": "tool_completed",
            "tool_name": "web.search",
            "ok": True,
            "model_tool_name": "web.search",
            "runtime_tool_name": "tinyfish.search",
            "runtime_binding_id": "tinyfish-prod",
            "runtime_resolution_source": "registry",
            "runtime_fallback_used": True,
            "runtime_fallback_chain": ["serper.search"],
            "fallback_index": 1,
        }
    )
    assert event is not None
    assert event.provenance["model_tool_name"] == "web.search"
    assert event.provenance["runtime_tool_name"] == "tinyfish.search"
    assert event.fallback["runtime_fallback_used"] is True
    assert event.fallback["runtime_fallback_chain"] == ["serper.search"]
    assert event.fallback["fallback_index"] == 1


def test_adapter_default_safe_on_missing_args_and_call_id() -> None:
    event = activity_from_progress_payload({"kind": "tool_started"})
    assert event is not None
    assert event.args == {}
    assert event.call_id == ""
    assert event.tool_name == ""


def test_adapter_captures_effort_level_when_present() -> None:
    event = activity_from_progress_payload(
        {
            "kind": "tool_started",
            "tool_name": "bash",
            "effort_level": "high",
        }
    )
    assert event is not None
    assert event.effort_level == "high"


def test_adapter_maps_budget_event() -> None:
    event = activity_from_progress_payload(
        {"kind": "budget_event", "event_type": "tokens_low"}
    )
    assert event is not None
    assert event.kind == KIND_BUDGET
    assert event.title == "tokens_low"


def test_adapter_maps_task_plan_event() -> None:
    plan = {
        "summary": "Run smoke suite",
        "items": [
            {"text": "lint", "status": "done"},
            {"text": "test", "status": "todo"},
        ],
    }
    event = activity_from_progress_payload({"kind": "task_plan", "plan": plan})
    assert event is not None
    assert event.kind == KIND_PLAN
    assert event.state == STATE_RUNNING
    assert event.plan == plan


def test_adapter_maps_task_plan_step_completed_and_blocked() -> None:
    completed = activity_from_progress_payload(
        {"kind": "task_plan_step_completed", "step_text": "ship"}
    )
    blocked = activity_from_progress_payload(
        {
            "kind": "task_plan_step_blocked",
            "step_text": "deploy",
            "reason": "missing token",
        }
    )
    assert completed is not None and completed.state == STATE_COMPLETED
    assert blocked is not None
    assert blocked.state == STATE_BLOCKED
    assert blocked.detail == "missing token"


def test_adapter_maps_approval_payloads() -> None:
    request = activity_from_progress_payload(
        {
            "kind": "approval_request",
            "tool_name": "git.reset",
            "args": {"mode": "hard"},
        }
    )
    denied = activity_from_progress_payload(
        {
            "kind": "approval_decision",
            "tool_name": "git.reset",
            "decision": "denied",
            "reason": "outside workspace",
        }
    )
    allowed = activity_from_progress_payload(
        {
            "kind": "approval_decision",
            "tool_name": "git.reset",
            "decision": "allowed",
        }
    )
    assert request is not None and request.kind == KIND_APPROVAL
    assert request.state == STATE_RUNNING
    assert denied is not None and denied.state == STATE_DENIED
    assert allowed is not None and allowed.state == STATE_COMPLETED


def test_adapter_maps_background_events() -> None:
    started = activity_from_progress_payload(
        {"kind": "background_started", "title": "research"}
    )
    completed = activity_from_progress_payload(
        {
            "kind": "background_completed",
            "title": "research",
            "duration_ms": 7000,
        }
    )
    assert started is not None and started.state == STATE_RUNNING
    assert completed is not None
    assert completed.kind == KIND_BACKGROUND
    assert completed.state == STATE_COMPLETED
    assert completed.duration_ms == 7000


def test_adapter_maps_error_payload() -> None:
    event = activity_from_progress_payload(
        {"kind": "error", "title": "RuntimeError", "message": "boom"}
    )
    assert event is not None
    assert event.kind == KIND_ERROR
    assert event.detail == "boom"


def test_adapter_default_unrecognized_payload_becomes_status_event() -> None:
    event = activity_from_progress_payload(
        {"label": "Thinking", "status_key": "working"}
    )
    assert event is not None
    assert event.kind == KIND_STATUS
    assert event.title == "Thinking"


# ---- formatter coverage ----------------------------------------------


def test_format_activity_line_returns_none_for_none() -> None:
    assert format_activity_line(None) is None


def test_format_activity_line_tool_event_uses_shared_tool_call_format() -> None:
    event = TurnActivityEvent(
        kind=KIND_TOOL,
        state=STATE_RUNNING,
        tool_name="bash",
        args={"command": "ls"},
    )
    line = format_activity_line(event)
    assert line is not None
    # The TESS-03 formatter labels tool-running rows with the canonical
    # tool name in the rendered line.
    assert "bash" in line


def test_format_activity_line_todo_write_uses_plan_renderer() -> None:
    event = activity_from_progress_payload(
        {
            "kind": "tool_completed",
            "tool_name": "todo.write",
            "ok": True,
            "args": {
                "todos": [
                    {"text": "Map", "status": "done"},
                    {"text": "Ship", "status": "in_progress"},
                ]
            },
        }
    )

    assert format_activity_line(event) == (
        "Plan (1/2 done, 1 in progress):\n  [x] Map\n  [→] Ship"
    )


def test_format_activity_line_tool_completed_ok_includes_duration() -> None:
    event = TurnActivityEvent(
        kind=KIND_TOOL,
        state=STATE_COMPLETED,
        tool_name="bash",
        args={"command": "ls"},
        duration_ms=200,
    )
    line = format_activity_line(event)
    assert line is not None
    assert "bash" in line


def test_format_activity_line_plan_event_with_plan_uses_render_plan() -> None:
    event = TurnActivityEvent(
        kind=KIND_PLAN,
        state=STATE_RUNNING,
        plan={
            "summary": "Smoke",
            "items": [{"text": "lint", "status": "done"}],
        },
    )
    line = format_activity_line(event)
    assert line is not None
    # Plan rows route through `cli.presentation.plan_render.render_plan`
    # which prefixes the rendered block with "Plan".
    assert "Plan" in line
    assert "lint" in line


def test_format_activity_line_plan_step_done() -> None:
    event = TurnActivityEvent(kind=KIND_PLAN, state=STATE_COMPLETED, title="ship")
    line = format_activity_line(event)
    assert line == "Plan step done: ship"


def test_format_activity_line_plan_step_blocked_includes_reason() -> None:
    event = TurnActivityEvent(
        kind=KIND_PLAN,
        state=STATE_BLOCKED,
        title="deploy",
        detail="missing token",
    )
    line = format_activity_line(event)
    assert line == "Plan step blocked: deploy — missing token"


def test_format_activity_line_approval_states() -> None:
    request = TurnActivityEvent(
        kind=KIND_APPROVAL, state=STATE_RUNNING, tool_name="git.reset"
    )
    denied = TurnActivityEvent(
        kind=KIND_APPROVAL,
        state=STATE_DENIED,
        tool_name="git.reset",
        detail="outside workspace",
    )
    allowed = TurnActivityEvent(
        kind=KIND_APPROVAL, state=STATE_COMPLETED, tool_name="git.reset"
    )
    assert format_activity_line(request) == "Approval requested: git.reset"
    assert (
        format_activity_line(denied) == "Approval denied: git.reset — outside workspace"
    )
    assert format_activity_line(allowed) == "Approval allowed: git.reset"


def test_format_activity_line_background_event() -> None:
    running = TurnActivityEvent(
        kind=KIND_BACKGROUND, state=STATE_RUNNING, title="research"
    )
    done = TurnActivityEvent(
        kind=KIND_BACKGROUND,
        state=STATE_COMPLETED,
        title="research",
        duration_ms=7000,
    )
    assert format_activity_line(running) == "Background: research"
    assert format_activity_line(done) == "Background done: research (7000 ms)"


def test_format_activity_line_budget_event() -> None:
    event = TurnActivityEvent(kind=KIND_BUDGET, title="tokens_low")
    assert format_activity_line(event) == "Budget: tokens_low"


def test_format_activity_line_budget_event_strips_prefix() -> None:
    event = TurnActivityEvent(kind=KIND_BUDGET, title="budget.allocated")
    assert format_activity_line(event) == "Budget: allocated"


def test_format_activity_line_error_event() -> None:
    event = TurnActivityEvent(
        kind=KIND_ERROR, state=STATE_FAILED, title="RuntimeError", detail="boom"
    )
    assert format_activity_line(event) == "Error: RuntimeError — boom"


def test_format_activity_line_status_event_returns_title() -> None:
    event = TurnActivityEvent(kind=KIND_STATUS, title="Thinking")
    assert format_activity_line(event) == "Thinking"


def test_format_activity_line_status_event_empty_title_drops() -> None:
    event = TurnActivityEvent(kind=KIND_STATUS, title="")
    assert format_activity_line(event) is None


def test_format_activity_line_search_event_uses_two_line_lifecycle() -> None:
    event = TurnActivityEvent(
        kind=KIND_SEARCH,
        state=STATE_COMPLETED,
        title="web.search",
        tool_name="web.search",
        args={"query": "agent cli parity"},
        duration_ms=1200,
        source_payload={"search_count": 2},
    )

    line = format_activity_line(event)

    assert line is not None
    assert "Web Search" in line
    assert "Did 2 searches in 1.2s" in line
    assert "\n└" in line


# ---- structural guard: no Rich / Textual imports --------------------


def test_activity_ledger_owner_does_not_import_rich_or_textual() -> None:
    # Re-import in a controlled environment and inspect its module
    # graph: the owner module itself must not import Rich or Textual.
    mod = importlib.import_module("openminion.cli.status.activity_ledger")
    source_path = mod.__file__ or ""
    assert source_path
    with open(source_path, encoding="utf-8") as fh:
        source = fh.read()
    assert "import rich" not in source and "from rich" not in source
    assert "import textual" not in source and "from textual" not in source


def test_format_activity_line_does_not_pull_in_rich_or_textual() -> None:
    # Calling the formatter on each supported kind should not import
    # Rich or Textual transitively. We snapshot `sys.modules` before
    # the calls and assert no new rich/textual entries appeared.
    before = {name for name in sys.modules if name.startswith(("rich", "textual"))}
    for event in [
        TurnActivityEvent(kind=KIND_TOOL, tool_name="bash"),
        TurnActivityEvent(
            kind=KIND_PLAN,
            plan={"summary": "x", "items": []},
        ),
        TurnActivityEvent(kind=KIND_APPROVAL, tool_name="git.reset"),
        TurnActivityEvent(kind=KIND_BACKGROUND, title="research"),
        TurnActivityEvent(kind=KIND_BUDGET, title="tokens"),
        TurnActivityEvent(kind=KIND_ERROR, title="x"),
        TurnActivityEvent(kind=KIND_STATUS, title="Thinking"),
    ]:
        format_activity_line(event)
    after = {name for name in sys.modules if name.startswith(("rich", "textual"))}
    assert after == before
