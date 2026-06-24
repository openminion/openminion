from __future__ import annotations

from openminion.cli.status.activity_ledger import (
    KIND_SEARCH,
    KIND_TOOL,
    STATE_COMPLETED,
    STATE_RUNNING,
    TurnActivityEvent,
    activity_from_progress_payload,
    format_activity_line,
    format_per_action_metrics_suffix,
)


# ---- adapter coverage -----------------------------------------------


def test_adapter_reads_tokens_delta_from_payload() -> None:
    event = activity_from_progress_payload(
        {
            "kind": "tool_completed",
            "tool_name": "bash",
            "ok": True,
            "duration_ms": 1200,
            "tokens_delta": 340,
        }
    )
    assert event is not None
    assert event.tokens_delta == 340


def test_adapter_defaults_tokens_delta_to_none_when_absent() -> None:
    event = activity_from_progress_payload(
        {"kind": "tool_started", "tool_name": "bash"}
    )
    assert event is not None
    assert event.tokens_delta is None


def test_adapter_drops_invalid_tokens_delta() -> None:
    event = activity_from_progress_payload(
        {
            "kind": "tool_completed",
            "tool_name": "bash",
            "ok": True,
            "tokens_delta": "not-an-int",
        }
    )
    assert event is not None
    assert event.tokens_delta is None


def test_adapter_carries_effort_level_alongside_tokens_delta() -> None:
    event = activity_from_progress_payload(
        {
            "kind": "tool_completed",
            "tool_name": "bash",
            "ok": True,
            "tokens_delta": 5000,
            "effort_level": "high",
        }
    )
    assert event is not None
    assert event.effort_level == "high"
    assert event.tokens_delta == 5000


# ---- suffix formatter ------------------------------------------------


def test_suffix_empty_when_no_metrics_populated() -> None:
    event = TurnActivityEvent(kind=KIND_TOOL, tool_name="bash")
    assert format_per_action_metrics_suffix(event) == ""


def test_suffix_renders_tokens_delta_only() -> None:
    event = TurnActivityEvent(kind=KIND_TOOL, tool_name="bash", tokens_delta=340)
    assert format_per_action_metrics_suffix(event) == "(↓ 340 tokens)"


def test_suffix_renders_effort_level_only() -> None:
    event = TurnActivityEvent(kind=KIND_TOOL, tool_name="bash", effort_level="high")
    assert format_per_action_metrics_suffix(event) == "(thinking with high effort)"


def test_suffix_renders_both_fields_joined_by_separator() -> None:
    event = TurnActivityEvent(
        kind=KIND_TOOL,
        tool_name="bash",
        tokens_delta=5000,
        effort_level="high",
    )
    assert (
        format_per_action_metrics_suffix(event)
        == "(↓ 5000 tokens · thinking with high effort)"
    )


def test_suffix_drops_zero_or_negative_tokens_delta() -> None:
    event_zero = TurnActivityEvent(kind=KIND_TOOL, tool_name="bash", tokens_delta=0)
    event_negative = TurnActivityEvent(
        kind=KIND_TOOL, tool_name="bash", tokens_delta=-5
    )
    assert format_per_action_metrics_suffix(event_zero) == ""
    assert format_per_action_metrics_suffix(event_negative) == ""


def test_suffix_strips_blank_effort_level() -> None:
    event = TurnActivityEvent(kind=KIND_TOOL, tool_name="bash", effort_level="   ")
    assert format_per_action_metrics_suffix(event) == ""


def test_suffix_none_event_returns_empty() -> None:
    assert format_per_action_metrics_suffix(None) == ""


# ---- format_activity_line integration ------------------------------


def test_format_activity_line_appends_suffix_to_tool_row() -> None:
    event = TurnActivityEvent(
        kind=KIND_TOOL,
        state=STATE_COMPLETED,
        tool_name="bash",
        args={"command": "ls"},
        duration_ms=1200,
        tokens_delta=340,
        effort_level="high",
    )
    line = format_activity_line(event)
    assert line is not None
    assert "bash" in line
    assert "↓ 340 tokens" in line
    assert "thinking with high effort" in line


def test_format_activity_line_omits_suffix_when_no_metrics() -> None:
    event = TurnActivityEvent(
        kind=KIND_TOOL,
        state=STATE_RUNNING,
        tool_name="bash",
        args={"command": "ls"},
    )
    line = format_activity_line(event)
    assert line is not None
    assert "↓" not in line
    assert "thinking" not in line


def test_format_activity_line_appends_suffix_to_search_row() -> None:
    event = TurnActivityEvent(
        kind=KIND_SEARCH,
        state=STATE_COMPLETED,
        tool_name="web.search",
        args={"query": "openminion"},
        duration_ms=2300,
        tokens_delta=1200,
    )
    line = format_activity_line(event)
    assert line is not None
    assert "web.search" in line
    assert "↓ 1200 tokens" in line


# ---- gateway streaming round-trip ----------------------------------


def test_gateway_stream_event_carries_tokens_delta_through_extractor() -> None:
    from openminion.services.gateway.streaming import (
        gateway_stream_event_from_progress,
    )

    event = gateway_stream_event_from_progress(
        {
            "kind": "tool_completed",
            "trace_id": "t1",
            "tool_name": "bash",
            "args": {"command": "ls"},
            "ok": True,
            "duration_ms": 1200,
            "tokens_delta": 340,
            "effort_level": "high",
        }
    )
    assert event is not None
    assert event.tokens_delta == 340
    assert event.effort_level == "high"


def test_gateway_stream_event_omits_tokens_delta_when_absent() -> None:
    from openminion.services.gateway.streaming import (
        gateway_stream_event_from_progress,
    )

    event = gateway_stream_event_from_progress(
        {
            "kind": "tool_started",
            "trace_id": "t1",
            "tool_name": "bash",
            "args": {},
        }
    )
    assert event is not None
    assert event.tokens_delta is None
    assert event.effort_level is None
