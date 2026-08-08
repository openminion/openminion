from __future__ import annotations

from openminion.modules.controlplane.pairing.store import ControlPlanePairingStore
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore


def test_expired_shared_pair_token_is_not_deleted_on_consume(tmp_path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    pairing = ControlPlanePairingStore(store)
    try:
        issued = pairing.issue_token(
            channel="telegram",
            expected_account_id="telegram-bot:user:1",
            expected_chat_key="telegram-bot:chat:1",
            scopes=["cp.message.read"],
            token="expiredToken1",
            ttl_seconds=60,
        )
        store._execute_count(
            "UPDATE cp_pair_tokens SET expires_at_ts = 1 WHERE token_hint = ?",
            (issued["token_hint"],),
        )

        consumed = pairing.consume_pair_token(
            channel="telegram",
            token=issued["token"],
            consumer_account_id="telegram-bot:user:1",
            consumer_chat_key="telegram-bot:chat:1",
        )

        assert consumed["ok"] is False
        assert consumed["reason"] == "expired_token"
        row = store._query_one(
            "SELECT used_at_ts FROM cp_pair_tokens WHERE token_hint = ?",
            (issued["token_hint"],),
        )
        assert row is not None
        assert row["used_at_ts"] is None
    finally:
        store.close()


def test_pair_attempt_count_ignores_old_rows_without_deleting_them(tmp_path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    pairing = ControlPlanePairingStore(store)
    try:
        pairing.record_attempt(
            channel="telegram",
            account_id="telegram-bot:user:1",
            chat_key="telegram-bot:chat:1",
            token="oldAttemptToken",
            outcome="invalid_token",
        )
        store._execute_count("UPDATE cp_pair_attempts SET attempted_at_ts = 1")
        pairing.record_attempt(
            channel="telegram",
            account_id="telegram-bot:user:1",
            chat_key="telegram-bot:chat:1",
            token="newAttemptToken",
            outcome="invalid_token",
        )

        recent = pairing.count_recent_attempts(
            channel="telegram",
            account_id="telegram-bot:user:1",
            since_ts=2,
        )
        total = store._query_one("SELECT COUNT(*) AS count FROM cp_pair_attempts")

        assert recent == 1
        assert total is not None
        assert total["count"] == 2
    finally:
        store.close()
