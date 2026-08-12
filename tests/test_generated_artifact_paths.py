from __future__ import annotations

import os
from pathlib import Path

from openminion.base.generated_paths import resolve_generated_root
from openminion.modules.identity.storage.store import SQLiteIdentityStore
from openminion.modules.storage.record_store import RecordStoreSQLite


def test_shared_fixture_isolates_all_openminion_roots(tmp_path: Path) -> None:
    data_root = tmp_path / ".openminion"

    assert Path(os.environ["OPENMINION_HOME"]) == tmp_path
    assert Path(os.environ["OPENMINION_DATA_ROOT"]) == data_root
    assert "OPENMINION_GENERATED_ROOT" not in os.environ
    assert resolve_generated_root() == data_root / "runtime"


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
