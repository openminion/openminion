from __future__ import annotations

from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore


_STALE_LOCKED_AT = "2000-01-01T00:00:00+00:00"


def test_inbox_stale_lock_reclaims_oldest_once(tmp_path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        stale_id, _ = store.enqueue_inbox(
            channel="telegram",
            chat_id="chat-1",
            channel_message_id="msg-stale",
            user_id="user-1",
            payload={"text": "stale first"},
        )
        fresh_id, _ = store.enqueue_inbox(
            channel="telegram",
            chat_id="chat-2",
            channel_message_id="msg-fresh",
            user_id="user-2",
            payload={"text": "fresh second"},
        )
        first_claim = store.claim_inbox(lock_owner="old-worker")
        assert first_claim is not None
        assert first_claim["inbox_id"] == stale_id
        store._execute_count(
            """
            UPDATE cp_inbox
            SET status='processing', lock_owner='old-worker', locked_at=?
            WHERE inbox_id=?
            """,
            (_STALE_LOCKED_AT, stale_id),
        )

        reclaimed = store.claim_inbox(lock_owner="new-worker", reclaim_ttl_s=1)

        assert reclaimed is not None
        assert reclaimed["inbox_id"] == stale_id
        assert reclaimed["attempts"] == 2
        assert reclaimed["lock_owner"] == "new-worker"
        fresh = store.get_inbox(fresh_id)
        assert fresh is not None
        assert fresh["attempts"] == 0
    finally:
        store.close()


def test_inbox_non_stale_lock_is_not_stolen(tmp_path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        locked_id, _ = store.enqueue_inbox(
            channel="telegram",
            chat_id="chat-1",
            channel_message_id="msg-locked",
            user_id="user-1",
            payload={"text": "locked"},
        )
        next_id, _ = store.enqueue_inbox(
            channel="telegram",
            chat_id="chat-2",
            channel_message_id="msg-next",
            user_id="user-2",
            payload={"text": "next"},
        )
        first_claim = store.claim_inbox(lock_owner="active-worker")
        assert first_claim is not None
        assert first_claim["inbox_id"] == locked_id

        second_claim = store.claim_inbox(lock_owner="other-worker", reclaim_ttl_s=3600)

        assert second_claim is not None
        assert second_claim["inbox_id"] == next_id
        locked = store.get_inbox(locked_id)
        assert locked is not None
        assert locked["lock_owner"] == "active-worker"
    finally:
        store.close()


def test_outbox_stale_lock_reclaims_oldest_once(tmp_path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        stale_id = store.enqueue_outbox(
            channel="telegram",
            chat_id="chat-1",
            payload={"text": "stale first"},
        )
        fresh_id = store.enqueue_outbox(
            channel="telegram",
            chat_id="chat-2",
            payload={"text": "fresh second"},
        )
        first_claim = store.claim_outbox(lock_owner="old-worker")
        assert first_claim is not None
        assert first_claim["outbox_id"] == stale_id
        store._execute_count(
            """
            UPDATE cp_outbox
            SET status='sending', lock_owner='old-worker', locked_at=?
            WHERE outbox_id=?
            """,
            (_STALE_LOCKED_AT, stale_id),
        )

        reclaimed = store.claim_outbox(lock_owner="new-worker", reclaim_ttl_s=1)

        assert reclaimed is not None
        assert reclaimed["outbox_id"] == stale_id
        assert reclaimed["attempts"] == 2
        assert reclaimed["lock_owner"] == "new-worker"
        fresh = store.get_outbox(fresh_id)
        assert fresh is not None
        assert fresh["attempts"] == 0
    finally:
        store.close()


def test_outbox_non_stale_lock_is_not_stolen(tmp_path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        locked_id = store.enqueue_outbox(
            channel="telegram",
            chat_id="chat-1",
            payload={"text": "locked"},
        )
        next_id = store.enqueue_outbox(
            channel="telegram",
            chat_id="chat-2",
            payload={"text": "next"},
        )
        first_claim = store.claim_outbox(lock_owner="active-worker")
        assert first_claim is not None
        assert first_claim["outbox_id"] == locked_id

        second_claim = store.claim_outbox(lock_owner="other-worker", reclaim_ttl_s=3600)

        assert second_claim is not None
        assert second_claim["outbox_id"] == next_id
        locked = store.get_outbox(locked_id)
        assert locked is not None
        assert locked["lock_owner"] == "active-worker"
    finally:
        store.close()
