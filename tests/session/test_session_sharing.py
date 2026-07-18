from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openminion.modules.session.sharing import (
    SessionShareDeniedError,
    SessionShareExpiredError,
    SessionShareService,
    SessionShareTokenTransportError,
    reject_forbidden_token_transport,
    verify_share_token,
)
from openminion.modules.session.storage import SQLiteSessionStore


def _store() -> SQLiteSessionStore:
    return SQLiteSessionStore(":memory:")


def test_session_share_returns_token_once_and_persists_hash_only() -> None:
    store = _store()
    sid = store.create_session(session_id="share-session")
    store.append_turn(sid, "user", "hello secret-ish user text", meta={"tool_inputs": "blocked"})
    service = SessionShareService(store)

    created = service.create_share(session_id=sid, created_by="alice")
    rows = store._record_store.query_rows("session_shares", where={"share_id": created.record.share_id})

    assert created.token
    assert rows[0]["token_hash"] != created.token
    assert created.token not in str(rows[0])
    assert rows[0]["token_hint"] == created.token[-8:]
    assert verify_share_token(token=created.token, expected_hash=rows[0]["token_hash"])


def test_session_share_projection_is_structural_and_audited() -> None:
    store = _store()
    sid = store.create_session(session_id="share-session")
    store.append_turn(sid, "user", "hello", meta={"tool_inputs": {"secret": "nope"}})
    service = SessionShareService(store)
    created = service.create_share(session_id=sid, created_by="alice")

    projection = service.access_share(share_id=created.record.share_id, token=created.token)

    assert projection["schema_version"] == "session_share_projection.v1"
    assert projection["readonly"] is True
    assert projection["turns"] == [
        {"turn_id": projection["turns"][0]["turn_id"], "role": "user", "text": "hello", "ts": projection["turns"][0]["ts"]}
    ]
    assert "tool_inputs" not in str(projection)
    assert service.access_count(created.record.share_id) == 1


def test_session_share_denies_wrong_expired_and_query_tokens() -> None:
    store = _store()
    sid = store.create_session(session_id="share-session")
    service = SessionShareService(store)
    now = datetime.now(timezone.utc)
    created = service.create_share(session_id=sid, created_by="alice", ttl_seconds=1, now=now)

    with pytest.raises(SessionShareDeniedError):
        service.access_share(share_id=created.record.share_id, token="wrong", now=now)
    with pytest.raises(SessionShareExpiredError):
        service.access_share(
            share_id=created.record.share_id,
            token=created.token,
            now=now + timedelta(seconds=5),
        )
    with pytest.raises(SessionShareTokenTransportError):
        reject_forbidden_token_transport(query_args={"token": [created.token]})


def test_session_share_revocation_and_rate_limit_fail_closed() -> None:
    store = _store()
    sid = store.create_session(session_id="share-session")
    service = SessionShareService(store, rate_limit=1)
    created = service.create_share(session_id=sid, created_by="alice")

    service.access_share(share_id=created.record.share_id, token=created.token)
    with pytest.raises(Exception) as exc_info:
        service.access_share(share_id=created.record.share_id, token=created.token)
    assert getattr(exc_info.value, "code", "") == "SESSION_SHARE_RATE_LIMITED"
