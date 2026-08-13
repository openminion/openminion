from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteSessionStore:
    session_store = SQLiteSessionStore(tmp_path / "tool-transcript.db")
    yield session_store
    session_store.close()


def _session(store: SQLiteSessionStore) -> str:
    return store.create_session(initial_agent_id="agent.main", profile_version="pv1")


def _requested_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "turn_scope_id": "turn-1",
        "call_id": "call-1",
        "canonical_name": "file.read",
        "sanitized_normalized_arguments": {
            "path": "/tmp/example.txt",
            "api_key": "must-not-persist",
        },
        "batch_index": 0,
        "depends_on": [],
    }
    payload.update(updates)
    return payload


def test_canonical_tool_events_are_ordered_linked_and_redacted(
    store: SQLiteSessionStore,
) -> None:
    session_id = _session(store)
    requested_id = store.append_event(
        session_id,
        event_type="tool.call.requested",
        payload=_requested_payload(),
    )
    completed_id = store.append_event(
        session_id,
        event_type="tool.call.completed",
        parent_event_id=requested_id,
        payload={
            "schema_version": 1,
            "turn_scope_id": "turn-1",
            "call_id": "call-1",
            "status": "success",
            "output": {
                "content": "provider_key=sk-fixture-1234567890abcdef",
                "authorization": "Bearer secret",
            },
        },
    )

    events = store.get_replay_events(
        session_id,
        event_types=["tool.call.requested", "tool.call.completed"],
    )

    assert [event["event_id"] for event in events] == [requested_id, completed_id]
    assert events[1]["parent_event_id"] == requested_id
    assert events[0]["payload"]["sanitized_normalized_arguments"] == {
        "path": "/tmp/example.txt",
        "api_key": "[REDACTED]",
    }
    assert events[1]["payload"]["output"]["authorization"] == "[REDACTED]"
    assert "sk-fixture" not in events[1]["payload"]["output"]["content"]
    assert "[REDACTED]" in events[1]["payload"]["output"]["content"]
    assert events[0]["redaction"] == "bounded"
    assert events[1]["redaction"] == "bounded"


def test_identical_retry_is_noop_and_conflicting_retry_fails(
    store: SQLiteSessionStore,
) -> None:
    session_id = _session(store)
    first_id = store.append_event(
        session_id,
        event_type="tool.call.requested",
        payload=_requested_payload(),
    )
    retry_id = store.append_event(
        session_id,
        event_type="tool.call.requested",
        payload=_requested_payload(),
    )

    assert retry_id == first_id
    tool_events = [
        event
        for event in store.get_replay_events(session_id)
        if event["event_type"] == "tool.call.requested"
    ]
    assert len(tool_events) == 1

    with pytest.raises(ValueError, match="idempotency conflict"):
        store.append_event(
            session_id,
            event_type="tool.call.requested",
            payload=_requested_payload(
                sanitized_normalized_arguments={"path": "/tmp/other.txt"}
            ),
        )


@pytest.mark.parametrize("event_type", ["tool.call.failed", "tool.call.error"])
def test_terminal_event_aliases_are_rejected(
    store: SQLiteSessionStore,
    event_type: str,
) -> None:
    session_id = _session(store)

    with pytest.raises(ValueError, match="unsupported tool terminal event"):
        store.append_event(
            session_id,
            event_type=event_type,
            payload={"call_id": "call-1"},
        )


@pytest.mark.parametrize("duplicated_field", ["canonical_name", "arguments"])
def test_terminal_result_rejects_call_owned_fields(
    store: SQLiteSessionStore,
    duplicated_field: str,
) -> None:
    session_id = _session(store)
    requested_id = store.append_event(
        session_id,
        event_type="tool.call.requested",
        payload=_requested_payload(),
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "turn_scope_id": "turn-1",
        "call_id": "call-1",
        "status": "success",
        "output": "hello",
        duplicated_field: {} if duplicated_field == "arguments" else "file.read",
    }

    with pytest.raises(ValueError, match="must not contain"):
        store.append_event(
            session_id,
            event_type="tool.call.completed",
            parent_event_id=requested_id,
            payload=payload,
        )


def test_terminal_result_requires_matching_requested_parent(
    store: SQLiteSessionStore,
) -> None:
    session_id = _session(store)
    requested_id = store.append_event(
        session_id,
        event_type="tool.call.requested",
        payload=_requested_payload(),
    )

    with pytest.raises(ValueError, match="call_id does not match"):
        store.append_event(
            session_id,
            event_type="tool.call.blocked",
            parent_event_id=requested_id,
            payload={
                "schema_version": 1,
                "turn_scope_id": "turn-1",
                "call_id": "call-other",
                "status": "error",
                "error": {"code": "TOOL_FAILED", "message": "failed"},
            },
        )


def test_one_terminal_result_is_authoritative_per_call(
    store: SQLiteSessionStore,
) -> None:
    session_id = _session(store)
    requested_id = store.append_event(
        session_id,
        event_type="tool.call.requested",
        payload=_requested_payload(),
    )
    store.append_event(
        session_id,
        event_type="tool.call.completed",
        parent_event_id=requested_id,
        payload={
            "schema_version": 1,
            "turn_scope_id": "turn-1",
            "call_id": "call-1",
            "status": "success",
            "output": "hello",
        },
    )

    with pytest.raises(ValueError, match="idempotency conflict"):
        store.append_event(
            session_id,
            event_type="tool.call.blocked",
            parent_event_id=requested_id,
            payload={
                "schema_version": 1,
                "turn_scope_id": "turn-1",
                "call_id": "call-1",
                "status": "timeout",
                "error": {"code": "TOOL_TIMEOUT", "message": "timed out"},
            },
        )


def test_replay_selects_canonical_or_legacy_lane_without_rewrite(
    store: SQLiteSessionStore,
) -> None:
    canonical_session = _session(store)
    store.append_event(
        canonical_session,
        event_type="tool.call.requested",
        payload=_requested_payload(),
    )

    legacy_session = _session(store)
    store.append_event(
        legacy_session,
        event_type="tool.request",
        payload={"tool_id": "file.read", "arguments": {"path": "/tmp/old.txt"}},
    )
    before = store.get_replay_events(legacy_session)

    canonical = store.get_tool_transcript(canonical_session)
    legacy = store.get_tool_transcript(legacy_session)

    assert canonical["transcript_lane"] == "canonical_events"
    assert [event["event_type"] for event in canonical["events"]] == [
        "tool.call.requested"
    ]
    assert legacy["transcript_lane"] == "legacy_history"
    assert legacy["causal_fidelity"] == "best_effort"
    assert store.get_replay_events(legacy_session) == before
