from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.session.security import (
    FernetSessionKeyRing,
    SessionEncryptionKeyError,
    decrypt_session_payload,
    encrypt_session_payload,
)
from openminion.modules.session.storage import (
    SQLiteSessionStore,
    SessionTurnBusyError,
    SessionTurnFenceError,
)


def test_daemon_death_recovers_after_lease_expiry_with_one_visible_result(
    tmp_path: Path,
) -> None:
    db = tmp_path / "session.db"
    store = SQLiteSessionStore(db)
    session_id = store.create_session(initial_agent_id="agent.main")
    first = store.acquire_session_turn_lease(
        session_id,
        owner="daemon-a",
        request_id="req-1",
        ttl_s=1,
        now_iso="2026-07-25T10:00:00+00:00",
    )
    store.append_turn(
        session_id,
        "user",
        "recover this turn",
        session_turn_fence_token=first.fence_token,
    )
    store.close()

    restarted = SQLiteSessionStore(db)
    with pytest.raises(SessionTurnBusyError):
        restarted.acquire_session_turn_lease(
            session_id,
            owner="daemon-b",
            request_id="req-2",
            ttl_s=30,
            now_iso="2026-07-25T10:00:00+00:00",
        )
    second = restarted.acquire_session_turn_lease(
        session_id,
        owner="daemon-b",
        request_id="req-2",
        ttl_s=30,
        now_iso="2026-07-25T10:00:02+00:00",
    )
    restarted.append_turn(
        session_id,
        "assistant",
        "recovered once",
        session_turn_fence_token=second.fence_token,
    )
    turns = restarted.list_turns(session_id)
    restarted.close()

    assistant_turns = [turn for turn in turns if turn["role"] == "assistant"]
    assert [turn["content"] for turn in assistant_turns] == ["recovered once"]


def test_duplicate_delivery_reuses_one_lease_and_result(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "session.db")
    session_id = store.create_session(initial_agent_id="agent.main")
    first = store.acquire_session_turn_lease(
        session_id,
        owner="daemon-a",
        request_id="same-request",
        ttl_s=30,
        now_iso="2026-07-25T10:00:00+00:00",
    )
    duplicate = store.acquire_session_turn_lease(
        session_id,
        owner="daemon-a",
        request_id="same-request",
        ttl_s=30,
        now_iso="2026-07-25T10:00:01+00:00",
    )
    store.append_turn(
        session_id,
        "assistant",
        "single result",
        session_turn_fence_token=duplicate.fence_token,
    )
    turns = store.list_turns(session_id)
    store.close()

    assert duplicate.fence_token == first.fence_token
    assert [turn["content"] for turn in turns if turn["role"] == "assistant"] == [
        "single result"
    ]


def test_stale_fence_cannot_write_after_takeover(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "session.db")
    session_id = store.create_session(initial_agent_id="agent.main")
    first = store.acquire_session_turn_lease(
        session_id,
        owner="daemon-a",
        request_id="req-a",
        ttl_s=1,
        now_iso="2026-07-25T10:00:00+00:00",
    )
    second = store.acquire_session_turn_lease(
        session_id,
        owner="daemon-b",
        request_id="req-b",
        ttl_s=30,
        now_iso="2026-07-25T10:00:02+00:00",
    )

    with pytest.raises(SessionTurnFenceError):
        store.append_event(
            session_id,
            event_type="turn.stale",
            payload={"status": "stale"},
            session_turn_fence_token=first.fence_token,
        )
    with pytest.raises(SessionTurnFenceError):
        store.put_working_state(
            session_id,
            state_inline={"status": "stale"},
            session_turn_fence_token=first.fence_token,
        )
    store.append_event(
        session_id,
        event_type="turn.recovered",
        payload={"status": "ok"},
        session_turn_fence_token=second.fence_token,
    )
    events = store.get_events(session_id)
    store.close()

    assert "turn.recovered" in [event["event_type"] for event in events]


def test_interrupted_tool_call_cannot_complete_from_old_fence(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "session.db")
    session_id = store.create_session(initial_agent_id="agent.main")
    first = store.acquire_session_turn_lease(
        session_id,
        owner="daemon-a",
        request_id="req-a",
        ttl_s=1,
        now_iso="2026-07-25T10:00:00+00:00",
    )
    store.append_event(
        session_id,
        event_type="tool.request",
        payload={"tool_id": "exec.run"},
        session_turn_fence_token=first.fence_token,
    )
    second = store.acquire_session_turn_lease(
        session_id,
        owner="daemon-b",
        request_id="req-b",
        ttl_s=30,
        now_iso="2026-07-25T10:00:02+00:00",
    )
    with pytest.raises(SessionTurnFenceError):
        store.append_event(
            session_id,
            event_type="tool.completed",
            payload={"tool_id": "exec.run"},
            session_turn_fence_token=first.fence_token,
        )
    store.append_event(
        session_id,
        event_type="tool.interrupted",
        payload={"tool_id": "exec.run", "recovered_by": "daemon-b"},
        session_turn_fence_token=second.fence_token,
    )
    event_types = [event["event_type"] for event in store.get_events(session_id)]
    store.close()

    assert [item for item in event_types if item.startswith("tool.")] == [
        "tool.request",
        "tool.interrupted",
    ]


def test_replay_events_survive_partial_restart(tmp_path: Path) -> None:
    db = tmp_path / "session.db"
    store = SQLiteSessionStore(db)
    session_id = store.create_session(initial_agent_id="agent.main")
    store.append_event(session_id, event_type="turn.user", payload={"text": "hi"})
    store.close()

    restarted = SQLiteSessionStore(db)
    restarted.append_event(
        session_id,
        event_type="turn.assistant",
        payload={"text": "hello"},
    )
    replay = restarted.get_replay_events(session_id)
    restarted.close()

    assert [
        event["event_type"]
        for event in replay
        if str(event["event_type"]).startswith("turn.")
    ] == [
        "turn.user",
        "turn.assistant",
    ]
    assert [
        event["seq"] for event in replay if str(event["event_type"]).startswith("turn.")
    ] == [2, 3]


def test_encrypted_restart_with_wrong_key_fails_closed() -> None:
    ring = FernetSessionKeyRing(active_key_id="k1")
    envelope = encrypt_session_payload(
        ring,
        payload={"text": "durable"},
        purpose="session.turn.content",
        record_identity={"session_id": "s1", "record_id": "r1"},
    )
    restarted_ring = FernetSessionKeyRing(active_key_id="k1")

    with pytest.raises(SessionEncryptionKeyError):
        decrypt_session_payload(
            restarted_ring,
            envelope,
            expected_identity={"session_id": "s1", "record_id": "r1"},
        )
