from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.session.storage import (
    SQLiteSessionStore,
    SessionTurnBusyError,
    SessionTurnFenceError,
)


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteSessionStore:
    session_store = SQLiteSessionStore(tmp_path / "turn-leases.db")
    yield session_store
    session_store.close()


def test_acquire_session_turn_lease_returns_monotonic_fence(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(initial_agent_id="agent.main")

    first = store.acquire_session_turn_lease(
        session_id,
        owner="worker-a",
        request_id="req-a",
        ttl_s=30,
        now_iso="2026-07-18T10:00:00+00:00",
    )
    assert first.session_id == session_id
    assert first.fence_token == 1

    assert store.release_session_turn_lease(
        session_id,
        owner="worker-a",
        fence_token=first.fence_token,
        now_iso="2026-07-18T10:00:03+00:00",
    )
    second = store.acquire_session_turn_lease(
        session_id,
        owner="worker-b",
        request_id="req-b",
        ttl_s=30,
        now_iso="2026-07-18T10:00:04+00:00",
    )
    assert second.fence_token == 2
    assert second.owner == "worker-b"


def test_duplicate_owner_request_renews_existing_lease(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(initial_agent_id="agent.main")

    first = store.acquire_session_turn_lease(
        session_id,
        owner="worker-a",
        request_id="same-req",
        ttl_s=10,
        now_iso="2026-07-18T10:00:00+00:00",
    )
    second = store.acquire_session_turn_lease(
        session_id,
        owner="worker-a",
        request_id="same-req",
        ttl_s=20,
        now_iso="2026-07-18T10:00:05+00:00",
    )

    assert second.fence_token == first.fence_token
    assert second.expires_at == "2026-07-18T10:00:25+00:00"


def test_distinct_request_is_busy_until_expiry(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(initial_agent_id="agent.main")
    store.acquire_session_turn_lease(
        session_id,
        owner="worker-a",
        request_id="req-a",
        ttl_s=60,
        now_iso="2026-07-18T10:00:00+00:00",
    )

    with pytest.raises(SessionTurnBusyError) as exc_info:
        store.acquire_session_turn_lease(
            session_id,
            owner="worker-b",
            request_id="req-b",
            ttl_s=60,
            now_iso="2026-07-18T10:00:05+00:00",
        )

    assert exc_info.value.code == "SESSION_TURN_BUSY"
    assert exc_info.value.retry_after_s >= 50


def test_stale_lease_can_be_taken_over_after_expiry(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(initial_agent_id="agent.main")
    first = store.acquire_session_turn_lease(
        session_id,
        owner="worker-a",
        request_id="req-a",
        ttl_s=1,
        now_iso="2026-07-18T10:00:00+00:00",
    )

    second = store.acquire_session_turn_lease(
        session_id,
        owner="worker-b",
        request_id="req-b",
        ttl_s=60,
        now_iso="2026-07-18T10:00:02+00:00",
    )

    assert second.fence_token == first.fence_token + 1
    with pytest.raises(SessionTurnFenceError):
        store.assert_session_turn_fence(session_id, fence_token=first.fence_token)
    store.assert_session_turn_fence(session_id, fence_token=second.fence_token)


def test_renew_and_release_require_matching_fence(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(initial_agent_id="agent.main")
    lease = store.acquire_session_turn_lease(
        session_id,
        owner="worker-a",
        request_id="req-a",
        ttl_s=10,
        now_iso="2026-07-18T10:00:00+00:00",
    )

    assert not store.renew_session_turn_lease(
        session_id,
        owner="worker-b",
        fence_token=lease.fence_token,
        now_iso="2026-07-18T10:00:01+00:00",
    )
    assert store.renew_session_turn_lease(
        session_id,
        owner="worker-a",
        fence_token=lease.fence_token,
        now_iso="2026-07-18T10:00:01+00:00",
    )
    assert not store.release_session_turn_lease(
        session_id,
        owner="worker-a",
        fence_token=lease.fence_token + 1,
        now_iso="2026-07-18T10:00:02+00:00",
    )
    assert store.release_session_turn_lease(
        session_id,
        owner="worker-a",
        fence_token=lease.fence_token,
        now_iso="2026-07-18T10:00:02+00:00",
    )
    with pytest.raises(SessionTurnFenceError):
        store.assert_session_turn_fence(session_id, fence_token=lease.fence_token)


def _replace_legacy_lease(store: SQLiteSessionStore, session_id: str) -> tuple[int, int]:
    first = store.acquire_session_turn_lease(
        session_id,
        owner="worker-a",
        request_id="req-a",
        ttl_s=60,
        now_iso="2026-07-18T10:00:00+00:00",
    )
    assert store.release_session_turn_lease(
        session_id,
        owner=first.owner,
        fence_token=first.fence_token,
        now_iso="2026-07-18T10:00:01+00:00",
    )
    second = store.acquire_session_turn_lease(
        session_id,
        owner="worker-b",
        request_id="req-b",
        ttl_s=60,
        now_iso="2026-07-18T10:00:02+00:00",
    )
    return first.fence_token, second.fence_token


def test_stale_legacy_turn_fence_rejects_protected_mutation_families(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(initial_agent_id="agent.main")
    stale_fence, active_fence = _replace_legacy_lease(store, session_id)

    stale_calls = [
        lambda: store.append_turn(
            session_id,
            "user",
            "stale",
            session_turn_fence_token=stale_fence,
        ),
        lambda: store.append_event(
            session_id,
            event_type="turn.stale",
            payload={"status": "stale"},
            session_turn_fence_token=stale_fence,
        ),
        lambda: store.put_working_state(
            session_id,
            state_inline={"status": "stale"},
            session_turn_fence_token=stale_fence,
        ),
        lambda: store.set_summary_base(
            session_id,
            "stale-base",
            session_turn_fence_token=stale_fence,
        ),
        lambda: store.append_summary_delta(
            session_id,
            "stale-delta",
            session_turn_fence_token=stale_fence,
        ),
        lambda: store.update_summary(
            session_id,
            "stale summary",
            based_on_seq=0,
            session_turn_fence_token=stale_fence,
        ),
        lambda: store.create_snapshot(
            session_id,
            session_turn_fence_token=stale_fence,
        ),
        lambda: store.update_derived_views(
            session_id,
            session_turn_fence_token=stale_fence,
        ),
        lambda: store.create_prompt_context(
            session_id,
            session_turn_fence_token=stale_fence,
        ),
        lambda: store.save_compression_checkpoint(
            session_id,
            "{}",
            session_turn_fence_token=stale_fence,
        ),
        lambda: store.save_seed_bundle(
            session_id,
            "bundle-a",
            "[]",
            0,
            session_turn_fence_token=stale_fence,
        ),
        lambda: store.create_run_record(
            session_id,
            session_turn_fence_token=stale_fence,
        ),
        lambda: store.add_message_ref(
            session_id,
            "assistant",
            content_inline="stale",
            session_turn_fence_token=stale_fence,
        ),
    ]
    for call in stale_calls:
        with pytest.raises(SessionTurnFenceError):
            call()

    turn_id = store.append_turn(
        session_id,
        "user",
        "active",
        session_turn_fence_token=active_fence,
    )
    event_id = store.append_event(
        session_id,
        event_type="turn.active",
        payload={"status": "active"},
        session_turn_fence_token=active_fence,
    )
    version = store.put_working_state(
        session_id,
        state_inline={"status": "active"},
        session_turn_fence_token=active_fence,
    )
    prompt_context_id = store.create_prompt_context(
        session_id,
        session_turn_fence_token=active_fence,
    )
    run_id = store.create_run_record(
        session_id,
        prompt_context_id=prompt_context_id,
        session_turn_fence_token=active_fence,
    )
    store.finish_run_record(
        run_id,
        status="completed",
        session_turn_fence_token=active_fence,
    )
    store.add_run_usage_delta(
        run_id,
        input_tokens=1,
        output_tokens=2,
        session_turn_fence_token=active_fence,
    )
    ref_id = store.add_message_ref(
        session_id,
        "assistant",
        run_id=run_id,
        event_id=event_id,
        content_inline="active",
        session_turn_fence_token=active_fence,
    )

    assert turn_id
    assert event_id
    assert version == 1
    assert prompt_context_id
    assert run_id
    assert ref_id
