from __future__ import annotations

import sqlite3
from pathlib import Path

from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore


def test_controlplane_pair_store_hashes_and_consumes_token(tmp_path: Path) -> None:
    db_path = tmp_path / "cp.db"
    store = SQLiteControlPlaneStore(db_path)
    try:
        issued = store.issue_pair_token(
            channel="test",
            expected_account_id="test:user:1",
            expected_chat_key="test:chat:2",
            scopes=["chat.interact"],
            token="abc_123",
            ttl_seconds=60,
            hash_pepper="pepper",
        )
        assert issued["token"] == "abc_123"
        assert issued["token_hash_prefix"]

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT token_hash, token_hint, scopes_json FROM cp_pair_tokens"
            ).fetchone()
        assert row is not None
        assert row[0] != "abc_123"
        assert row[1] == "abc_"
        assert "abc_123" not in str(row)

        consumed = store.consume_pair_token(
            channel="test",
            token="abc_123",
            consumer_account_id="test:user:1",
            consumer_chat_key="test:chat:2",
            hash_pepper="pepper",
        )
        assert consumed["ok"] is True
        assert consumed["reason"] == "paired"
        assert consumed["scopes"] == ["chat.interact"]

        replay = store.consume_pair_token(
            channel="test",
            token="abc_123",
            consumer_account_id="test:user:1",
            consumer_chat_key="test:chat:2",
            hash_pepper="pepper",
        )
        assert replay["ok"] is False
        assert replay["reason"] == "already_used"
    finally:
        store.close()


def test_controlplane_pair_store_records_attempt_counts(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        store.record_pair_attempt(
            channel="test",
            account_id="test:user:1",
            chat_key="test:chat:2",
            token="bad",
            outcome="invalid_token",
            hash_pepper="pepper",
        )
        assert (
            store.count_recent_pair_attempts(
                channel="test", account_id="test:user:1", since_ts=0
            )
            == 1
        )
        assert (
            store.count_recent_pair_attempts_for_chat(
                channel="test", chat_key="test:chat:2", since_ts=0
            )
            == 1
        )
    finally:
        store.close()
