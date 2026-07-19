from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteSessionStore:
    db_path = tmp_path / "sess-sidecar.db"
    session_store = SQLiteSessionStore(db_path)
    yield session_store
    session_store.close()


def test_storage_status_reports_sqlite_health(store: SQLiteSessionStore) -> None:
    status = store.storage_status()
    assert "sqlite_ok" in status
    assert status["sqlite_ok"] is True
    assert "fallback_mode" in status
    assert status["session_turn_leases"] == {
        "schema_version": "session_turn_lease.v1",
        "active_count": 0,
        "expired_unreleased_count": 0,
        "max_fence_token": 0,
    }


def test_reindex_sidecars_is_noop_without_sidecar_events(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    event_id = store.append_event(
        session_id,
        event_type="task.opened",
        payload={"task_id": "t1", "title": "normal"},
    )
    report = store.reindex_sidecars()

    assert report["inserted"] == 0
    assert event_id


def test_blob_store_root_uses_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = (tmp_path / ".openminion").resolve()
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))

    db_path = data_root / "session" / "sessions.db"
    store = SQLiteSessionStore(db_path)
    try:
        blob_root = store._hybrid_store.blob_store.root_dir
        assert blob_root == (data_root / "storage").resolve()
    finally:
        store.close()
