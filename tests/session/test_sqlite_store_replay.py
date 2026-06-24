from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteSessionStore:
    db_path = tmp_path / "sess-replay.db"
    session_store = SQLiteSessionStore(db_path)
    yield session_store
    session_store.close()


def test_get_replay_events_filters_and_ranges(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    store.append_event(
        session_id,
        event_type="turn.user",
        payload={"text": "hello"},
    )
    store.append_event(
        session_id,
        event_type="turn.assistant",
        payload={"text": "hi"},
    )
    store.append_event(
        session_id,
        event_type="tool.request",
        payload={"tool_id": "search"},
    )
    store.append_event(
        session_id,
        event_type="tool.completed",
        payload={"tool_id": "search"},
    )

    all_events = store.get_replay_events(session_id)
    ranged = store.get_replay_events(session_id, from_seq=2, to_seq=3)
    assert [event["seq"] for event in ranged] == [2, 3]
    assert ranged == [event for event in all_events if 2 <= event["seq"] <= 3]

    filtered = store.get_replay_events(session_id, event_types=["tool.request"])
    assert len(filtered) == 1
    assert filtered[0]["event_type"] == "tool.request"


def test_get_resume_state_includes_context_and_clarify_events(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    checkpoint_id = store.save_compression_checkpoint(session_id, bundle_json="{}")
    seed_id = store.save_seed_bundle(
        session_id,
        source_bundle_id="seed-1",
        sections_json="[]",
        total_tokens=0,
    )
    prompt_context_id = store.create_prompt_context(
        session_id,
        seed_bundle_id=seed_id,
        checkpoint_id=checkpoint_id,
        prefix_hash="hash-1",
        meta={"purpose": "resume"},
    )

    store.put_working_state(
        session_id,
        state_inline={
            "phase": "run",
            "cursor": 2,
            "trace_id": "trace-1",
            "status": "waiting",
            "unresolved_clarify_items": ["q1", "q2"],
            "clarify_responses": {"q1": "a1"},
            "pending_llm_clarify_context": {
                "original_user_input": "what's rather at china?",
                "inferred_goal": "weather",
                "known_context": {"place": "China"},
                "clarify_question": "Did you mean the weather in China, or something else?",
            },
            "pending_turn_context": {
                "original_user_request": "save the server code",
                "active_work_summary": "Waiting for the target path before writing the file.",
                "known_context": {"cwd": "/tmp/openminion"},
                "missing_fields": ["path"],
                "artifact_refs": ["artifact:previous"],
                "response_preferences": {"language": "en"},
            },
        },
    )

    store.append_event(
        session_id,
        event_type="brain.clarify.requested",
        payload={"question": "q1"},
        trace_id="trace-1",
    )
    store.append_event(
        session_id,
        event_type="brain.clarify.answered",
        payload={"answer": "a1"},
        trace_id="trace-1",
    )
    store.append_event(
        session_id,
        event_type="brain.clarify.context_stored",
        payload={"reason": "decision_sidecar"},
        trace_id="trace-1",
    )

    resume_state = store.get_resume_state(session_id)

    assert resume_state["session_id"] == session_id
    assert resume_state["prompt_context"]["prompt_context_id"] == prompt_context_id
    assert resume_state["latest_checkpoint"]["checkpoint_id"] == checkpoint_id
    assert resume_state["latest_seed"]["seed_id"] == seed_id
    assert resume_state["resume_keys"]["unresolved_clarify_count"] == 2
    assert resume_state["resume_keys"]["clarify_response_count"] == 1
    assert resume_state["resume_keys"]["pending_llm_clarify_context"] is True
    assert resume_state["resume_keys"]["pending_turn_context"] is True

    clarify_types = {event["event_type"] for event in resume_state["clarify_events"]}
    assert clarify_types == {
        "brain.clarify.requested",
        "brain.clarify.answered",
        "brain.clarify.context_stored",
    }


def test_backfill_events_imports_and_skips_invalid(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    result = store.backfill_events(
        session_id,
        [
            {"event_type": "task.opened", "payload": {"task_id": "t1"}},
            {"type": "task.updated", "payload": {"task_id": "t1", "status": "done"}},
            {"payload": {"task_id": "t2"}},
        ],
    )

    assert result["imported"] == 2
    assert result["skipped"] == 1
    assert result["total"] == 3

    events = store.get_events(session_id)
    event_types = {event["event_type"] for event in events}
    assert "task.opened" in event_types
    assert "task.updated" in event_types
