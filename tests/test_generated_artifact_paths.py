from __future__ import annotations

from pathlib import Path

from openminion.modules.identity.storage.store import SQLiteIdentityStore
from openminion.modules.storage.record_store import RecordStoreSQLite


def test_record_store_memory_mode_does_not_create_disk_files(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store = RecordStoreSQLite(":memory:")
    try:
        assert store.query_dicts("SELECT 1 AS ok") == [{"ok": 1}]
    finally:
        store.close()

    assert not (tmp_path / ":memory:").exists()
    assert not (tmp_path / ":memory:-wal").exists()
    assert not (tmp_path / ":memory:-shm").exists()


def test_identity_store_memory_mode_does_not_create_disk_files(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store = SQLiteIdentityStore(":memory:")
    try:
        assert store.list_profiles() == []
    finally:
        store.close()

    assert not (tmp_path / ":memory:").exists()
    assert not (tmp_path / ":memory:-wal").exists()
    assert not (tmp_path / ":memory:-shm").exists()
