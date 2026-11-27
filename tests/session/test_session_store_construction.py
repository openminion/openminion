from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

import pytest

from openminion.modules.session.runtime.factory import build_module_session_store
from openminion.modules.session.storage import store as store_module
from openminion.modules.session.storage.store import (
    PostgresSessionStore,
    SQLiteSessionStore,
)
from openminion.modules.storage.backends.blob_store import BlobStoreFS
from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig
from openminion.modules.storage.backends.hybrid_store import HybridStore
from openminion.modules.storage.record_store import RecordStore, RecordStoreSQLite


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


def test_sqlite_session_store_closes_only_its_default_artifactctl(
    tmp_path: Path,
    monkeypatch,
) -> None:
    close_calls: list[str] = []

    class ArtifactCtl:
        def close(self) -> None:
            close_calls.append("close")

    monkeypatch.setattr(
        "openminion.modules.session.storage.store.create_default_artifactctl",
        ArtifactCtl,
    )
    store = SQLiteSessionStore(tmp_path / "default.db")
    store._resolve_artifactctl()
    store.close()
    store.close()
    assert close_calls == ["close"]

    injected = ArtifactCtl()
    store = SQLiteSessionStore(tmp_path / "injected.db", artifactctl=injected)
    store.close()
    assert close_calls == ["close"]


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

    def wrapped(cls, *, config, registry=None, module_id=None):  # type: ignore[no-untyped-def]
        captured["config"] = config
        captured["module_id"] = module_id
        return original(config=config, registry=registry, module_id=module_id)

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
        # The module id selects a PostgreSQL schema only; SQLite ignores it (#446).
        assert captured["module_id"] == "session"
        assert isinstance(store, SQLiteSessionStore)
        assert store._hybrid_store.record_store is store._record_store  # noqa: SLF001
    finally:
        store.close()


def test_memory_store_close_removes_owned_temp_root_and_is_idempotent() -> None:
    store = SQLiteSessionStore(":memory:")
    temp_root = store._temp_root
    assert temp_root is not None
    assert temp_root.exists()

    store.close()

    assert store._temp_root is None
    assert store._owned_temp_root is None
    assert not temp_root.exists()
    store.close()


def test_close_preserves_persistent_paths_caller_hybrid_store_and_reassigned_path(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "persistent" / "sessions.db"
    persistent_store = SQLiteSessionStore(db_path)
    persistent_store.close()
    assert db_path.exists()
    assert db_path.parent.exists()

    caller_storage = tmp_path / "caller-storage"
    caller_fallback = tmp_path / "caller-fallback"
    record_store = RecordStoreSQLite(":memory:", wal=True)
    caller_hybrid = HybridStore(
        record_store=record_store,
        blob_store=BlobStoreFS(caller_storage),
        fallback_root=caller_fallback,
        default_namespace="sessctl",
    )
    injected_store = SQLiteSessionStore(
        ":memory:",
        record_store=record_store,
        hybrid_store=caller_hybrid,
    )
    injected_store.close()
    assert caller_storage.exists()
    assert caller_fallback.exists()

    external_dir = tmp_path / "external"
    external_dir.mkdir()
    memory_store = SQLiteSessionStore(":memory:")
    owned_root = memory_store._owned_temp_root
    assert owned_root is not None
    memory_store._temp_root = external_dir
    memory_store.close()
    assert not owned_root.exists()
    assert external_dir.exists()
    assert memory_store._temp_root is external_dir


def test_memory_store_init_failure_cleans_owned_resources_and_preserves_exception(
    tmp_path: Path,
    monkeypatch,
) -> None:
    created_roots: list[Path] = []
    real_builder = store_module._build_default_hybrid_store

    def tracking_builder(**kwargs):
        hybrid_store, temp_root = real_builder(**kwargs)
        if temp_root is not None:
            created_roots.append(temp_root)
        return hybrid_store, temp_root

    captured_conns: list[sqlite3.Connection] = []
    orig_configure = SQLiteSessionStore._configure_connection

    def tracking_configure(self: SQLiteSessionStore) -> None:
        assert self._conn is not None
        captured_conns.append(self._conn)
        orig_configure(self)

    def fail_migrate(self: SQLiteSessionStore) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(store_module, "_build_default_hybrid_store", tracking_builder)
    monkeypatch.setattr(SQLiteSessionStore, "_configure_connection", tracking_configure)
    monkeypatch.setattr(SQLiteSessionStore, "_migrate", fail_migrate)

    with pytest.raises(RuntimeError, match="boom"):
        SQLiteSessionStore(":memory:")

    assert len(created_roots) == 1
    assert not created_roots[0].exists()
    assert len(captured_conns) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        captured_conns[0].execute("SELECT 1")

    injected_record_store = RecordStoreSQLite(":memory:", wal=True)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            SQLiteSessionStore(":memory:", record_store=injected_record_store)
        assert len(created_roots) == 2
        assert not created_roots[1].exists()
        assert injected_record_store.connection.execute("SELECT 1").fetchone()[0] == 1
    finally:
        injected_record_store.close()


def test_memory_store_early_init_failure_before_temp_root_and_in_hybrid_builder(
    monkeypatch,
) -> None:
    captured_conns: list[sqlite3.Connection] = []

    def fail_configure(self: SQLiteSessionStore) -> None:
        assert self._conn is not None
        captured_conns.append(self._conn)
        raise RuntimeError("early boom")

    monkeypatch.setattr(SQLiteSessionStore, "_configure_connection", fail_configure)
    with pytest.raises(RuntimeError, match="early boom"):
        SQLiteSessionStore(":memory:")

    assert len(captured_conns) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        captured_conns[0].execute("SELECT 1")
    monkeypatch.undo()

    created_dirs: list[Path] = []
    real_mkdtemp = store_module.tempfile.mkdtemp

    def tracking_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created_dirs.append(Path(path).resolve())
        return path

    def fail_hybrid_store(**kwargs):
        raise RuntimeError("hybrid boom")

    monkeypatch.setattr(store_module.tempfile, "mkdtemp", tracking_mkdtemp)
    monkeypatch.setattr(store_module, "HybridStore", fail_hybrid_store)
    with pytest.raises(RuntimeError, match="hybrid boom"):
        SQLiteSessionStore(":memory:")

    assert len(created_dirs) == 1
    assert not created_dirs[0].exists()


def test_close_failure_still_cleans_temp_root_and_is_not_masked(monkeypatch) -> None:
    store = SQLiteSessionStore(":memory:")
    temp_root = store._temp_root
    assert temp_root is not None

    def fail_close() -> None:
        raise RuntimeError("close boom")

    def fail_rmtree(_path: Path) -> None:
        raise OSError("rm boom")

    monkeypatch.setattr(store._record_store, "close", fail_close)
    monkeypatch.setattr(store_module.shutil, "rmtree", fail_rmtree)

    try:
        with pytest.raises(RuntimeError, match="close boom"):
            store.close()
        assert temp_root.exists()
        assert store._owned_temp_root == temp_root
    finally:
        monkeypatch.undo()
        store.close()
    assert not temp_root.exists()
    assert store._owned_temp_root is None

    store2 = SQLiteSessionStore(":memory:")
    temp_root2 = store2._temp_root
    assert temp_root2 is not None
    monkeypatch.setattr(store2._record_store, "close", fail_close)
    try:
        with pytest.raises(RuntimeError, match="close boom"):
            store2.close()
        assert not temp_root2.exists()
        assert store2._owned_temp_root is None
    finally:
        monkeypatch.undo()
        store2.close()


def test_close_deletion_failure_preserves_ownership_for_later_close(
    monkeypatch,
) -> None:
    store = SQLiteSessionStore(":memory:")
    temp_root = store._temp_root
    assert temp_root is not None
    assert temp_root.exists()

    calls: list[Path] = []
    real_rmtree = store_module.shutil.rmtree
    should_fail = True

    def controlled_rmtree(path: Path, *args, **kwargs) -> None:
        calls.append(Path(path))
        if should_fail:
            raise PermissionError("locked")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(store_module.shutil, "rmtree", controlled_rmtree)
    try:
        with pytest.raises(PermissionError, match="locked"):
            store.close()
        assert calls == [temp_root]
        assert temp_root.exists()
        assert store._owned_temp_root == temp_root
        assert store._temp_root == temp_root

        should_fail = False
        store.close()
        assert calls == [temp_root, temp_root]
        assert not temp_root.exists()
        assert store._owned_temp_root is None
        assert store._temp_root is None

        store.close()
        assert calls == [temp_root, temp_root]
    finally:
        should_fail = False
        monkeypatch.undo()
        store.close()

    store_absent = SQLiteSessionStore(":memory:")
    absent_root = store_absent._temp_root
    assert absent_root is not None
    real_rmtree(absent_root)
    assert not absent_root.exists()

    absent_calls: list[Path] = []

    def tracking_rmtree(path: Path, *args, **kwargs) -> None:
        absent_calls.append(Path(path))
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(store_module.shutil, "rmtree", tracking_rmtree)
    try:
        store_absent.close()
        assert absent_calls == [absent_root]
        assert store_absent._owned_temp_root is None
        assert store_absent._temp_root is None

        store_absent.close()
        assert absent_calls == [absent_root]
    finally:
        monkeypatch.undo()
        store_absent.close()


def test_postgres_session_store_init_failure_cleans_owned_temp_root_without_closing_injected_store(
    monkeypatch,
) -> None:
    close_calls: list[str] = []

    class FakePostgresRecordStore:
        def capabilities(self) -> dict[str, bool]:
            return {"raw_sql": False}

        def execute_count(self, *_args, **_kwargs) -> int:
            return 0

        def query_dicts(self, *_args, **_kwargs) -> list[dict[str, object]]:
            return []

        def close(self) -> None:
            close_calls.append("closed")

    created_roots: list[Path] = []
    real_builder = store_module._build_default_hybrid_store

    def tracking_builder(**kwargs):
        hybrid_store, temp_root = real_builder(**kwargs)
        if temp_root is not None:
            created_roots.append(temp_root)
        return hybrid_store, temp_root

    def fail_metadata(self: PostgresSessionStore) -> None:
        raise RuntimeError("pg metadata boom")

    monkeypatch.setattr(store_module, "_build_default_hybrid_store", tracking_builder)
    monkeypatch.setattr(
        PostgresSessionStore, "_finalize_module_metadata", fail_metadata
    )

    fake_record_store = FakePostgresRecordStore()
    with pytest.raises(RuntimeError, match="pg metadata boom"):
        PostgresSessionStore(
            ":memory:", record_store=cast(RecordStore, fake_record_store)
        )

    assert len(created_roots) == 1
    assert not created_roots[0].exists()
    assert close_calls == []
