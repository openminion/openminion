from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.storage.runtime.session_store.turn_leases import (
    RuntimeSessionTurnBusyError,
    RuntimeSessionTurnFenceError,
)
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database


@pytest.fixture()
def store(tmp_path: Path) -> SessionStore:
    db_path = tmp_path / "runtime-turn-leases.db"
    migrate_database(db_path)
    connection = connect_database(db_path)
    session_store = SessionStore(connection)
    yield session_store
    connection.close()


def test_runtime_session_store_turn_lease_busy_and_release(store: SessionStore) -> None:
    session = store.resolve_session(
        agent_id="agent.main",
        channel="console",
        target="user",
        session_id="session-lease",
    )
    first = store.acquire_session_turn_lease(
        session.id,
        owner="worker-a",
        request_id="req-a",
        ttl_s=60,
        now_iso="2026-07-18T10:00:00+00:00",
    )

    with pytest.raises(RuntimeSessionTurnBusyError):
        store.acquire_session_turn_lease(
            session.id,
            owner="worker-b",
            request_id="req-b",
            ttl_s=60,
            now_iso="2026-07-18T10:00:01+00:00",
        )

    assert store.release_session_turn_lease(
        session.id,
        owner=first.owner,
        fence_token=first.fence_token,
        now_iso="2026-07-18T10:00:02+00:00",
    )
    second = store.acquire_session_turn_lease(
        session.id,
        owner="worker-b",
        request_id="req-b",
        ttl_s=60,
        now_iso="2026-07-18T10:00:03+00:00",
    )
    assert second.fence_token == first.fence_token + 1


def _replace_active_lease(store: SessionStore, session_id: str) -> tuple[int, int]:
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


def test_stale_runtime_turn_fence_rejects_message_event_and_context_writes(
    store: SessionStore,
) -> None:
    session = store.resolve_session(
        agent_id="agent.main",
        channel="console",
        target="user",
        session_id="session-stale-fence",
    )
    stale_fence, active_fence = _replace_active_lease(store, session.id)

    with pytest.raises(RuntimeSessionTurnFenceError):
        store.append_message(
            session_id=session.id,
            role="inbound",
            body="stale",
            session_turn_fence_token=stale_fence,
        )
    with pytest.raises(RuntimeSessionTurnFenceError):
        store.append_event(
            session_id=session.id,
            event_type="turn.stale",
            payload={"status": "stale"},
            session_turn_fence_token=stale_fence,
        )
    with pytest.raises(RuntimeSessionTurnFenceError):
        store.update_session_context(
            session_id=session.id,
            rolling_summary="stale",
            session_turn_fence_token=stale_fence,
        )

    message = store.append_message(
        session_id=session.id,
        role="inbound",
        body="active",
        session_turn_fence_token=active_fence,
    )
    event = store.append_event(
        session_id=session.id,
        event_type="turn.active",
        payload={"status": "active"},
        session_turn_fence_token=active_fence,
    )
    context = store.update_session_context(
        session_id=session.id,
        rolling_summary="active",
        session_turn_fence_token=active_fence,
    )

    assert message.body == "active"
    assert event.event_type == "turn.active"
    assert context.rolling_summary == "active"
