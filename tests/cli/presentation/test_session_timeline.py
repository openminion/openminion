from __future__ import annotations

import json

import pytest

from openminion.cli.presentation.session_timeline import (
    TimelineEntry,
    build_timeline,
    render_timeline,
    timeline_payload,
)
from openminion.modules.storage.runtime.session_store import EventRecord


def _event(
    event_id: int,
    event_type: str,
    payload: object = None,
    *,
    created_at: str = "2026-03-22T10:00:00+00:00",
    canonical_event_id: str | None = None,
) -> EventRecord:
    return EventRecord(
        id=event_id,
        session_id="sess-1",
        event_type=event_type,
        payload=payload,  # type: ignore[arg-type]
        created_at=created_at,
        canonical_event_id=canonical_event_id,
    )


def test_build_timeline_orders_by_event_id_and_assigns_sequence() -> None:
    entries = build_timeline(
        [
            _event(3, "session.closed"),
            _event(1, "turn.user", {"text": "first"}),
            _event(2, "tool.call", {"tool_name": "shell"}),
        ]
    )

    assert [entry.seq for entry in entries] == [1, 2, 3]
    assert [entry.event_type for entry in entries] == [
        "turn.user",
        "tool.call",
        "session.closed",
    ]


def test_message_classification_uses_text_and_role_suffix() -> None:
    (entry,) = build_timeline([_event(1, "turn.user", {"text": "hello"})])

    assert entry.kind == "message"
    assert entry.actor == "user"
    assert entry.summary == "hello"
    assert entry.timestamp == "2026-03-22 10:00:00"


def test_message_payload_role_fills_actor_when_suffix_is_not_a_role() -> None:
    (entry,) = build_timeline(
        [_event(1, "message.note", {"role": "assistant", "message": "note text"})]
    )

    assert entry.kind == "message"
    assert entry.actor == "assistant"
    assert entry.summary == "note text"


def test_turn_event_without_message_suffix_is_not_a_message() -> None:
    (entry,) = build_timeline([_event(1, "turn.started")])

    assert entry.kind == "event"
    assert entry.summary == "turn.started"


def test_tool_classification_uses_payload_name_and_status() -> None:
    (entry,) = build_timeline(
        [_event(1, "tool.call", {"tool_name": "shell", "status": "started"})]
    )

    assert entry.kind == "tool"
    assert entry.actor == "tool"
    assert entry.status == "started"
    assert entry.summary == "tool shell started"


def test_tool_status_falls_back_to_type_suffix() -> None:
    (entry,) = build_timeline([_event(1, "tool.result", {"name": "file.read"})])

    assert entry.status == "result"
    assert entry.summary == "tool file.read result"


def test_tool_without_payload_fields_still_renders() -> None:
    (entry,) = build_timeline([_event(1, "tool.call")])

    assert entry.kind == "tool"
    assert entry.actor == "tool"
    assert entry.status == "call"
    assert entry.summary == "tool call"


def test_approval_classification_includes_tool() -> None:
    (entry,) = build_timeline(
        [_event(1, "approval.request", {"status": "pending", "tool": "shell"})]
    )

    assert entry.kind == "approval"
    assert entry.actor == "policy"
    assert entry.status == "pending"
    assert entry.summary == "pending (tool shell)"


def test_policy_decision_classifies_as_approval() -> None:
    (entry,) = build_timeline(
        [_event(1, "policy.decision", {"decision": "denied", "tool_name": "exec"})]
    )

    assert entry.kind == "approval"
    assert entry.summary == "denied (tool exec)"


def test_handoff_summary_uses_from_and_to_agents() -> None:
    (entry,) = build_timeline(
        [_event(1, "handoff.request", {"from_agent": "alpha", "to_agent": "beta"})]
    )

    assert entry.kind == "handoff"
    assert entry.actor == "alpha"
    assert entry.summary == "alpha -> beta"


def test_delegation_classifies_as_handoff() -> None:
    (entry,) = build_timeline(
        [_event(1, "delegation.start", {"source": "alpha", "target": "beta"})]
    )

    assert entry.kind == "handoff"
    assert entry.summary == "alpha -> beta"


def test_unknown_event_keeps_type_and_status() -> None:
    (entry,) = build_timeline([_event(1, "session.context.budget", {"status": "ok"})])

    assert entry.kind == "event"
    assert entry.actor == ""
    assert entry.status == "ok"
    assert entry.summary == "session.context.budget ok"


@pytest.mark.parametrize("payload", [None, "raw payload text", ["raw", "list"]])
def test_malformed_payloads_do_not_crash_or_leak(payload: object) -> None:
    (entry,) = build_timeline([_event(1, "tool.call", payload)])

    assert entry.kind == "tool"
    assert entry.summary == "tool call"
    assert "raw" not in json.dumps(timeline_payload(session_id="s", entries=[entry]))


def test_none_payload_fields_are_treated_as_missing() -> None:
    (entry,) = build_timeline(
        [
            _event(
                1,
                "turn.user",
                {"text": None, "role": None, "status": None, "actor": None},
            )
        ]
    )

    assert entry.summary == "turn.user"
    assert entry.actor == "user"
    assert entry.status == ""
    assert "None" not in entry.summary


def test_text_is_truncated_to_160_characters() -> None:
    (entry,) = build_timeline([_event(1, "turn.user", {"text": "x" * 200})])

    assert len(entry.summary) == 160
    assert entry.summary.endswith("...")


def test_unicode_text_is_preserved() -> None:
    (entry,) = build_timeline([_event(1, "turn.assistant", {"content": "héllo 世界"})])

    assert entry.summary == "héllo 世界"


def test_newlines_and_whitespace_are_collapsed() -> None:
    (entry,) = build_timeline([_event(1, "turn.user", {"text": "a\n\n  b\tc "})])

    assert entry.summary == "a b c"


def test_ansi_and_control_characters_are_stripped() -> None:
    (entry,) = build_timeline(
        [_event(1, "turn.user", {"text": "\x1b[31mred\x1b[0m\x00 done"})]
    )

    assert entry.summary == "red done"
    assert "\x1b" not in entry.summary
    assert "\x00" not in entry.summary


def test_render_header_includes_latest_count() -> None:
    entries = build_timeline([_event(1, "turn.user", {"text": "one"})])

    assert render_timeline(session_id="sess-1", entries=entries, total=10) == (
        "Session: sess-1\n"
        "Events: 1 (latest 1 of 10)\n"
        "2026-03-22 10:00:00  message   user: one"
    )


def test_render_empty_timeline() -> None:
    assert render_timeline(session_id="sess-1", entries=[]) == (
        "No events for session 'sess-1'."
    )


def test_render_pads_kind_to_eight_characters() -> None:
    entries = [
        TimelineEntry(
            seq=1,
            timestamp="2026-03-22 10:00:00",
            kind="tool",
            actor="tool",
            summary="tool shell started",
            status="started",
            event_type="tool.call",
            event_id="1",
        )
    ]

    assert "2026-03-22 10:00:00  tool      tool shell started" in render_timeline(
        session_id="sess-1", entries=entries
    )


def test_render_sanitizes_session_id() -> None:
    rendered = render_timeline(
        session_id="\x1b[31msess\x1b[0m\x07", entries=[], total=None
    )

    assert rendered == "No events for session 'sess'."


def test_timeline_payload_shape_and_no_raw_payload() -> None:
    entries = build_timeline(
        [
            _event(
                1,
                "turn.user",
                {"text": "hello", "api_key": "sk-live-secret"},
                created_at="2026-03-22T10:00:00+00:00",
            )
        ]
    )

    payload = timeline_payload(session_id="sess-1", entries=entries, total=4)

    assert payload["ok"] is True
    assert payload["session_id"] == "sess-1"
    assert payload["event_count"] == 4
    assert payload["shown_count"] == 1
    (entry_payload,) = payload["entries"]
    assert set(entry_payload) == {
        "seq",
        "timestamp",
        "kind",
        "actor",
        "summary",
        "status",
        "event_type",
        "event_id",
    }
    assert entry_payload["seq"] == 1
    assert entry_payload["summary"] == "hello"
    encoded = json.dumps(payload)
    assert "sk-live-secret" not in encoded
    assert "api_key" not in encoded


def test_event_id_prefers_canonical_id_and_falls_back_to_numeric_id() -> None:
    canonical, numeric = build_timeline(
        [
            _event(1, "turn.user", {"text": "one"}, canonical_event_id="evt-1"),
            _event(2, "turn.user", {"text": "two"}),
        ]
    )

    assert canonical.event_id == "evt-1"
    assert numeric.event_id == "2"


def test_malformed_timestamp_is_sanitized_and_preserved() -> None:
    (entry,) = build_timeline(
        [_event(1, "turn.user", {"text": "hi"}, created_at="\x1b[31mnot-a-time")]
    )

    assert entry.timestamp == "not-a-time"


def test_missing_timestamp_is_empty() -> None:
    (entry,) = build_timeline([_event(1, "turn.user", {"text": "hi"}, created_at="")])

    assert entry.timestamp == ""


def test_agent_execution_event_is_not_a_handoff() -> None:
    (entry,) = build_timeline([_event(1, "agent.execution.started")])

    assert entry.kind == "event"
    assert entry.summary == "agent.execution.started"


def test_agent_bound_is_a_handoff() -> None:
    (entry,) = build_timeline([_event(1, "agent.bound", {"to_agent": "reviewer"})])

    assert entry.kind == "handoff"
    assert entry.summary == "reviewer"


def test_long_invalid_timestamp_is_capped() -> None:
    (entry,) = build_timeline(
        [_event(1, "turn.user", {"text": "hi"}, created_at="x" * 100)]
    )

    assert len(entry.timestamp) == 19


def test_empty_event_type_gets_a_fallback_summary() -> None:
    (entry,) = build_timeline([_event(1, "")])

    assert entry.summary == "unknown event"
