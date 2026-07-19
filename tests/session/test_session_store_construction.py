from __future__ import annotations

from pathlib import Path

from openminion.modules.session.runtime.factory import build_module_session_store
from openminion.modules.session.storage.store import SQLiteSessionStore
from openminion.modules.storage.backends.blob_store import BlobStoreFS
from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig
from openminion.modules.storage.backends.hybrid_store import HybridStore
from openminion.modules.storage.record_store import RecordStoreSQLite


def test_sqlite_session_store_default_construction_sets_pragmas(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    try:
        assert isinstance(store._record_store, RecordStoreSQLite)
        assert isinstance(store._hybrid_store, HybridStore)
        assert store._conn is store._record_store.connection
        assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert (
            str(store._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            == "wal"
        )  # noqa: SLF001
    finally:
        store.close()


def test_sqlite_session_store_uses_injected_record_store_for_all_substores(
    tmp_path: Path,
) -> None:
    record_store = RecordStoreSQLite(tmp_path / "sessions.db", wal=True)
    hybrid_store = HybridStore(
        record_store=record_store,
        blob_store=BlobStoreFS(tmp_path / "storage"),
        fallback_root=tmp_path,
        default_namespace="sessctl",
    )
    store = SQLiteSessionStore(
        tmp_path / "sessions.db",
        record_store=record_store,
        hybrid_store=hybrid_store,
    )
    try:
        assert store._record_store is record_store
        assert store._event_store._rs is record_store
        assert store._slice_queries._record_store is record_store
        assert store._event_writer._record_store is record_store
        assert store._cron_store._record_store is record_store
        assert store._state_store._rs is record_store
        assert store._summary_store._rs is record_store
        assert store._context_store._rs is record_store
        assert store._run_store._rs is record_store
        assert store._turn_lease_store._record_store is record_store
        assert store._session_helper._record_store is record_store
        assert store._replay_helper._record_store is record_store
    finally:
        store.close()


def test_build_module_session_store_uses_storage_engine_for_sqlite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    original = StorageEngine.from_config

    def wrapped(cls, *, config, registry=None):  # type: ignore[no-untyped-def]
        captured["config"] = config
        return original(config=config, registry=registry)

    monkeypatch.setattr(StorageEngine, "from_config", classmethod(wrapped))

    store = build_module_session_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "sessions.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        database_path=tmp_path / "sessions.db",
    )
    try:
        assert "config" in captured
        assert isinstance(store, SQLiteSessionStore)
        assert store._hybrid_store.record_store is store._record_store  # noqa: SLF001
    finally:
        store.close()
