from __future__ import annotations

import json

import pytest

from openminion.cli.presentation.session_timeline import (
    TimelineEntry,
    build_timeline,
    render_timeline,
    timeline_payload,
)
from openminion.modules.storage.runtime.session_store import EventRecord, MessageRecord
from openminion.services.agent.execution.required.metadata import (
    invalid_tool_arguments_metadata,
)

TS = "2026-03-22T10:00:00+00:00"


def _message(
    role: str,
    body: str,
    *,
    metadata: object = None,
    created_at: str = TS,
    message_id: str = "msg-1",
    rowid: int = 1,
) -> MessageRecord:
    return MessageRecord(
        id=message_id,
        session_id="sess-1",
        conversation_id="",
        thread_id="",
        attach_id="",
        role=role,
        body=body,
        metadata=metadata,  # type: ignore[arg-type]
        created_at=created_at,
        rowid=rowid,
    )


def _event(
    event_id: int,
    event_type: str,
    payload: object = None,
    *,
    created_at: str = TS,
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


def _tool_results(*results: object) -> dict[str, str]:
    return {"tool_results": json.dumps(list(results))}


def test_message_roles_and_event_identity() -> None:
    entries = build_timeline(
        messages=[
            _message("inbound", "hello", message_id="in-1"),
            _message("outbound", "done", message_id="out-1", rowid=2),
            _message("tool.note", "note", message_id="note-1", rowid=3),
        ]
    )

    assert [entry.actor for entry in entries] == ["user", "assistant", "tool.note"]
    assert [entry.event_type for entry in entries] == [
        "message.inbound",
        "message.outbound",
        "message.tool.note",
    ]
    assert [entry.summary for entry in entries] == ["hello", "done", "note"]
    assert [entry.event_id for entry in entries] == ["in-1", "out-1", "note-1"]
    assert all(entry.kind == "message" and entry.status == "" for entry in entries)


def test_tool_entries_come_from_outbound_metadata() -> None:
    entries = build_timeline(
        messages=[
            _message(
                "outbound",
                "done",
                metadata=_tool_results(
                    {
                        "call_id": "call-1",
                        "tool_name": "probe_tool",
                        "ok": True,
                        "data": {"result": "probe-ok-A-104"},
                    }
                ),
            )
        ]
    )

    (tool, message) = entries
    assert message.kind == "message"
    assert tool.kind == "tool"
    assert tool.actor == "tool"
    assert tool.status == "ok"
    assert tool.event_type == "tool.result.reported"
    assert tool.event_id == "call-1"
    assert tool.summary == "tool probe_tool reported ok"


def test_tool_entries_fall_back_to_cumulative_metadata() -> None:
    entries = build_timeline(
        messages=[
            _message(
                "outbound",
                "done",
                metadata={
                    "tool_calls_cumulative": [
                        {"tool_name": "file.read", "ok": True, "content": "bytes"}
                    ]
                },
            )
        ]
    )

    assert entries[0].kind == "tool"
    assert entries[0].summary == "tool file.read reported ok"


@pytest.mark.parametrize(
    "metadata",
    [
        {"tool_results": "not json"},
        {"tool_results": {"tool_name": "x"}},
        {"tool_results": {"tool_name": "x"}, "tool_calls_cumulative": []},
        {"tool_results": None},
    ],
)
def test_malformed_or_missing_tool_metadata_keeps_the_message(
    metadata: object,
) -> None:
    entries = build_timeline(messages=[_message("outbound", "done", metadata=metadata)])

    assert [entry.kind for entry in entries] == ["message"]


def test_tool_metadata_list_ignores_non_mapping_entries() -> None:
    entries = build_timeline(
        messages=[
            _message(
                "outbound",
                "done",
                metadata={"tool_results": ["raw", {"tool_name": "ok", "ok": True}]},
            )
        ]
    )

    assert [entry.kind for entry in entries] == ["tool", "message"]
    assert entries[0].summary == "tool ok reported ok"


def test_tool_summary_keeps_only_name_outcome_and_bounded_error_code() -> None:
    entries = build_timeline(
        messages=[
            _message(
                "outbound",
                "done",
                metadata=_tool_results(
                    {
                        "tool_name": "a",
                        "ok": True,
                        "data": {"private_token": "SYNTHETIC-TIMELINE-SECRET"},
                        "content": "arbitrary content",
                    },
                    {
                        "tool_name": "b",
                        "ok": False,
                        "content": "arbitrary content",
                        "error": "arbitrary error message",
                    },
                    {
                        "tool_name": "c",
                        "ok": False,
                        "error": "arbitrary error message",
                        "error_code": "E_TIMEOUT",
                    },
                    {
                        "tool_name": "d",
                        "ok": False,
                        "data": {"error_code": "invalid_arguments"},
                    },
                ),
            )
        ]
    )

    assert [entry.summary for entry in entries[:4]] == [
        "tool a reported ok",
        "tool b reported failed",
        "tool c reported failed (E_TIMEOUT)",
        "tool d reported failed (invalid_arguments)",
    ]
    assert [entry.event_type for entry in entries[:4]] == ["tool.result.reported"] * 4
    assert "SYNTHETIC-TIMELINE-SECRET" not in json.dumps(
        [entry.summary for entry in entries]
    )
    assert "arbitrary" not in json.dumps([entry.summary for entry in entries])


def test_invalid_tool_arguments_metadata_reports_failed_without_execution() -> None:
    metadata = invalid_tool_arguments_metadata(
        tool_name="probe_tool",
        missing_fields_csv="order_id,quantity",
    )

    entries = build_timeline(messages=[_message("outbound", "done", metadata=metadata)])

    (tool, message) = entries
    assert metadata["tool_execution_count"] == "0"
    assert message.kind == "message"
    assert tool.kind == "tool"
    assert tool.status == "failed"
    assert tool.event_type == "tool.result.reported"
    assert tool.summary == "tool probe_tool reported failed (invalid_arguments)"


def test_tool_without_a_name_uses_a_generic_name() -> None:
    entries = build_timeline(
        messages=[_message("outbound", "done", metadata=_tool_results({"ok": True}))]
    )

    assert entries[0].summary == "tool unknown reported ok"
    assert entries[0].event_id == "msg-1:tool:0"


def test_tool_rows_share_the_outbound_observation_time() -> None:
    entries = build_timeline(
        messages=[
            _message(
                "outbound",
                "done",
                metadata={
                    "run_id": "run-9",
                    "tool_results": [
                        {"tool_name": "probe_alpha", "ok": True},
                        {"tool_name": "probe_beta", "ok": True},
                    ],
                },
                created_at="2026-03-22T10:00:05+00:00",
            )
        ],
        events=[
            _event(
                1,
                "run.waiting_tool",
                {"run_id": "run-9"},
                created_at="2026-03-22T10:00:09+00:00",
            )
        ],
    )

    assert [entry.kind for entry in entries] == ["tool", "tool", "message"]
    assert [entry.timestamp for entry in entries] == [
        "2026-03-22 10:00:05",
        "2026-03-22 10:00:05",
        "2026-03-22 10:00:05",
    ]
    assert not any(entry.event_type == "run.waiting_tool" for entry in entries)


def test_tool_sorts_before_the_outbound_message_on_a_timestamp_tie() -> None:
    entries = build_timeline(
        messages=[
            _message(
                "outbound",
                "done",
                metadata={"tool_results": [{"tool_name": "probe_tool", "ok": True}]},
            )
        ]
    )

    assert [entry.kind for entry in entries] == ["tool", "message"]


@pytest.mark.parametrize(
    "event_type",
    [
        "run.queued",
        "run.running",
        "run.responding",
        "run.waiting_tool",
        "response.persisted",
        "response.delivered",
    ],
)
def test_exact_duplicate_progress_events_are_omitted(event_type: str) -> None:
    assert build_timeline(events=[_event(1, event_type, {"body": "x"})]) == []


@pytest.mark.parametrize(
    "event_type",
    [
        "run.failed",
        "run.cancelled",
        "run.completed",
        "turn.outcome",
        "thread.decision",
        "session.context.budget",
        "memory.capture.result",
        "knowledge_graph.query.completed",
        "run.future_state",
        "memory.future.observation",
        "knowledge_graph.query.future",
    ],
)
def test_outcome_and_observation_events_stay_bounded_event_rows(
    event_type: str,
) -> None:
    (entry,) = build_timeline(events=[_event(1, event_type, {"body": "x"})])

    assert entry.kind == "event"
    assert entry.actor == ""
    assert entry.status == ""
    assert entry.summary == event_type
    assert entry.event_type == event_type


def test_unknown_event_stays_an_honest_event_row() -> None:
    (entry,) = build_timeline(events=[_event(1, "session.renamed", {"new": "x"})])

    assert entry.kind == "event"
    assert entry.actor == ""
    assert entry.status == ""
    assert entry.summary == "session.renamed"
    assert entry.event_type == "session.renamed"


def test_approval_prompt_maps_body_to_pending_approval() -> None:
    (entry,) = build_timeline(
        events=[
            _event(
                1,
                "policy_confirmation_prompt",
                {"body": "Policy confirmation required. Run exec?", "run_id": "r"},
                created_at="2026-03-22T10:00:02+00:00",
            )
        ]
    )

    assert entry.kind == "approval"
    assert entry.actor == "policy"
    assert entry.status == "pending"
    assert entry.summary == "Policy confirmation required. Run exec?"
    assert entry.event_type == "policy_confirmation_prompt"


def test_handoff_maps_actual_agent_switched_payload() -> None:
    (entry,) = build_timeline(
        events=[
            _event(
                1,
                "agent.switched",
                {
                    "from_agent_id": "agent.alpha",
                    "to_agent_id": "agent.beta",
                    "reason": "handoff",
                },
            )
        ]
    )

    assert entry.kind == "handoff"
    assert entry.actor == "agent.alpha"
    assert entry.summary == "agent.alpha -> agent.beta (handoff)"


def test_handoff_without_reason_or_target_keeps_a_generic_summary() -> None:
    (entry,) = build_timeline(
        events=[_event(1, "agent.switched", {"from_agent_id": "agent.alpha"})]
    )

    assert entry.kind == "handoff"
    assert entry.summary == "agent.alpha"


def test_agent_bound_is_an_event_not_a_handoff() -> None:
    (entry,) = build_timeline(
        events=[
            _event(
                1,
                "agent.bound",
                {
                    "agent_id": "agent.beta",
                    "profile_version": "pv2",
                    "reason": "selected",
                },
            )
        ]
    )

    assert entry.kind == "event"
    assert entry.actor == "agent.beta"
    assert entry.summary == "agent bound agent.beta (profile pv2)"


def test_entries_sort_by_parsed_timestamp_across_sources() -> None:
    entries = build_timeline(
        messages=[
            _message("inbound", "first", message_id="m1", created_at=TS),
            _message(
                "outbound",
                "last",
                message_id="m2",
                created_at="2026-03-22T10:00:09+00:00",
            ),
        ],
        events=[
            _event(
                1,
                "agent.bound",
                {"agent_id": "a"},
                created_at="2026-03-22T10:00:05+00:00",
            )
        ],
    )

    assert [entry.summary for entry in entries] == [
        "first",
        "agent bound a",
        "last",
    ]
    assert [entry.seq for entry in entries] == [1, 2, 3]


def test_equal_timestamps_use_kind_priority_then_source_index() -> None:
    entries = build_timeline(
        messages=[
            _message(
                "outbound",
                "assistant",
                metadata={
                    "tool_results": [{"tool_name": "t", "ok": True}],
                },
            )
        ],
        events=[_event(1, "agent.bound", {"agent_id": "a"})],
    )

    assert [entry.kind for entry in entries] == ["tool", "message", "event"]


def test_malformed_timestamps_fall_back_to_source_order() -> None:
    entries = build_timeline(
        messages=[
            _message("inbound", "no ts", message_id="m1", created_at="not-a-time"),
            _message("inbound", "also no ts", message_id="m2", created_at=""),
        ]
    )

    assert [entry.summary for entry in entries] == ["no ts", "also no ts"]
    assert [entry.timestamp for entry in entries] == ["not-a-time", ""]


def test_render_header_reports_fetched_total_and_latest_count() -> None:
    entries = build_timeline(
        messages=[
            _message("inbound", "one", message_id="m1"),
            _message("inbound", "two", message_id="m2"),
        ]
    )

    assert render_timeline(session_id="sess-1", entries=entries[1:], total=2) == (
        "Session: sess-1\n"
        "Events: 2 (latest 1)\n"
        "2026-03-22 10:00:00  message   user: two"
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
            summary="tool shell reported ok",
            status="ok",
            event_type="tool.result.reported",
            event_id="1",
        )
    ]

    assert "2026-03-22 10:00:00  tool      tool shell reported ok" in render_timeline(
        session_id="sess-1", entries=entries
    )


def test_render_sanitizes_session_id() -> None:
    rendered = render_timeline(
        session_id="\x1b[31msess\x1b[0m\x07", entries=[], total=None
    )

    assert rendered == "No events for session 'sess'."


def test_timeline_payload_shape_and_no_raw_metadata() -> None:
    entries = build_timeline(
        messages=[
            _message(
                "outbound",
                "done",
                metadata={
                    "api_key": "sk-live-secret",
                    **_tool_results(
                        {
                            "tool_name": "probe_tool",
                            "ok": True,
                            "content": "ok",
                            "data": {
                                "private_token": "SYNTHETIC-TIMELINE-SECRET",
                            },
                        }
                    ),
                },
                created_at=TS,
            )
        ]
    )

    payload = timeline_payload(session_id="sess-1", entries=entries, total=4)

    assert payload["ok"] is True
    assert payload["session_id"] == "sess-1"
    assert payload["event_count"] == 4
    assert payload["shown_count"] == 2
    (tool_payload, message_payload) = payload["entries"]
    assert set(tool_payload) == {
        "seq",
        "timestamp",
        "kind",
        "actor",
        "summary",
        "status",
        "event_type",
        "event_id",
    }
    assert tool_payload["seq"] == 1
    assert tool_payload["summary"] == "tool probe_tool reported ok"
    assert tool_payload["event_type"] == "tool.result.reported"
    assert message_payload["summary"] == "done"
    encoded = json.dumps(payload)
    assert "sk-live-secret" not in encoded
    assert "SYNTHETIC-TIMELINE-SECRET" not in encoded
    assert "private_token" not in encoded
    assert "api_key" not in encoded
    assert "tool_results" not in encoded


def test_event_id_prefers_canonical_id_and_falls_back_to_numeric_id() -> None:
    canonical, numeric = build_timeline(
        events=[
            _event(1, "session.first", canonical_event_id="evt-1"),
            _event(2, "session.second"),
        ]
    )

    assert canonical.event_id == "evt-1"
    assert numeric.event_id == "2"


def test_non_mapping_event_payloads_do_not_crash() -> None:
    (entry,) = build_timeline(
        events=[_event(1, "agent.bound", ["raw", "list"], created_at="")]
    )

    assert entry.summary == "agent bound"
    assert entry.timestamp == ""


def test_text_is_truncated_once_to_160_characters() -> None:
    (entry,) = build_timeline(messages=[_message("inbound", "x" * 200)])

    assert len(entry.summary) == 160
    assert entry.summary.endswith("...")


def test_unicode_is_preserved_and_controls_are_stripped() -> None:
    entries = build_timeline(
        messages=[
            _message("inbound", "héllo 世界"),
            _message(
                "inbound",
                "\x1b[31mred\x1b[0m\x00 done\n\n  next",
                message_id="m2",
                rowid=2,
            ),
        ]
    )

    assert entries[0].summary == "héllo 世界"
    assert entries[1].summary == "red done next"
    assert "\x1b" not in entries[1].summary
    assert "\x00" not in entries[1].summary


def test_empty_tool_results_falls_back_to_cumulative_metadata() -> None:
    entries = build_timeline(
        messages=[
            _message(
                "outbound",
                "done",
                metadata={
                    "tool_results": [],
                    "tool_calls_cumulative": [
                        {"tool_name": "file.read", "ok": True, "content": "bytes"}
                    ],
                },
            )
        ]
    )

    assert entries[0].kind == "tool"
    assert entries[0].summary == "tool file.read reported ok"


def test_data_and_content_are_not_summarized() -> None:
    entries = build_timeline(
        messages=[
            _message(
                "outbound",
                "done",
                metadata=_tool_results(
                    {"tool_name": "probe", "ok": True, "data": {}, "content": "bytes"}
                ),
            )
        ]
    )

    assert entries[0].summary == "tool probe reported ok"
