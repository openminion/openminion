from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openminion.modules.session.retention import (
    SessionRetentionBlockedError,
    SessionRetentionPolicy,
    SessionRetentionService,
    SessionRetentionSnapshotChangedError,
)
from openminion.modules.session.sharing import SessionShareService
from openminion.modules.session.storage import SQLiteSessionStore


def _old() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()


def test_retention_dry_run_and_purge_delete_dependent_rows() -> None:
    store = SQLiteSessionStore(":memory:")
    sid = store.create_session(session_id="retention-old")
    store.append_turn(sid, "user", "delete me")
    store.update_summary(sid, "summary", based_on_seq=1)
    store._record_store.update_rows("sessions", {"session_id": sid}, {"updated_at": _old(), "status": "closed"})
    service = SessionRetentionService(store)
    plan = service.dry_run(policy=SessionRetentionPolicy(inactivity_ttl_seconds=1, closed_retention_seconds=1))

    assert [item.session_id for item in plan.candidates] == [sid]
    result = service.purge(plan)

    assert result["purged_session_count"] == 1
    assert store.get_session(sid) is None
    assert store.list_turns(sid) == []


def test_retention_blocks_active_hold_lease_and_share() -> None:
    store = SQLiteSessionStore(":memory:")
    sid = store.create_session(session_id="retention-blocked")
    store._record_store.update_rows("sessions", {"session_id": sid}, {"updated_at": _old(), "status": "closed"})
    SessionShareService(store).create_share(session_id=sid, created_by="alice", ttl_seconds=600)
    store.acquire_session_turn_lease(sid, owner="worker", request_id="r1", ttl_s=600)
    service = SessionRetentionService(store)
    service.add_hold(session_id=sid, reason="legal")
    store._record_store.update_rows("sessions", {"session_id": sid}, {"updated_at": _old(), "status": "closed"})
    plan = service.dry_run(policy=SessionRetentionPolicy(inactivity_ttl_seconds=1, closed_retention_seconds=1))

    assert set(plan.candidates[0].blockers) == {"retention_hold", "active_turn_lease", "active_share"}
    with pytest.raises(SessionRetentionBlockedError):
        service.purge(plan)


def test_retention_changed_snapshot_refuses_purge() -> None:
    store = SQLiteSessionStore(":memory:")
    sid = store.create_session(session_id="retention-drift")
    store._record_store.update_rows("sessions", {"session_id": sid}, {"updated_at": _old(), "status": "closed"})
    service = SessionRetentionService(store)
    plan = service.dry_run(policy=SessionRetentionPolicy(inactivity_ttl_seconds=1, closed_retention_seconds=1))
    store.create_session(session_id="newer")
    store._record_store.update_rows("sessions", {"session_id": "newer"}, {"updated_at": _old(), "status": "closed"})

    with pytest.raises(SessionRetentionSnapshotChangedError):
        service.purge(plan)
