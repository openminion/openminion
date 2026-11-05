"""Runtime storage closes what it opened when its construction fails.

`StorageEngine.from_config` owns its backends until it returns the engine, and
`build_runtime_storage` owns the engine until it returns its context. On a later
failure each closes them as `StorageEngine.close()` does, and the caller gets
the original exception. The caller's telemetry service stays open.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from openminion.base.version import OPENMINION_VERSION
from openminion.modules.storage import (
    StorageEngine,
    StorageEngineConfig,
    default_backend_registry,
)
from openminion.modules.storage import engine as storage_engine
from openminion.modules.storage.record_store import RecordStoreSQLite
from openminion.modules.storage.runtime import context as storage_context
from openminion.modules.storage.runtime.context import build_runtime_storage
from openminion.modules.telemetry import storage_hook
from openminion.modules.telemetry.service import TelemetryService
from tests._failed_start_scenarios import automatic_gc_off, connection_is_closed


class _Injected(RuntimeError):
    """The error a test injects into a construction step."""


@pytest.fixture(autouse=True)
def _no_automatic_gc() -> Iterator[None]:
    with automatic_gc_off():
        yield


def _failing(error: BaseException) -> Callable[..., Any]:
    def fail(*_args: object, **_kwargs: object) -> Any:
        raise error

    return fail


class _RecordStore(RecordStoreSQLite):
    def __init__(self, path: Path, closes: list[str]) -> None:
        super().__init__(path)
        self.closes = closes

    def close(self) -> None:
        self.closes.append("record")
        super().close()


class _VectorStore:
    contract_version = "v1"

    def __init__(self, closes: list[str]) -> None:
        self.closes = closes

    def upsert(self, vectors: Any, metadata: Any, ids: Any, namespace: Any = None):
        return None

    def search(
        self,
        query_vector: Any,
        top_k: int = 10,
        filters: Any = None,
        namespace: Any = None,
    ):
        return []

    def delete(self, ids: Any, namespace: Any = None) -> bool:
        return True

    def list_namespaces(self) -> list[str]:
        return []

    def namespace_stats(self, namespace: str) -> dict[str, Any]:
        return {"namespace": namespace, "count": 0}

    def count(self, namespace: Any = None) -> int:
        return 0

    def healthcheck(self) -> dict[str, Any]:
        return {"ok": True}

    def describe_backend(self) -> dict[str, Any]:
        return {
            "backend_id": "vector.counted",
            "version": OPENMINION_VERSION,
            "planes_supported": {"vector"},
            "capabilities": {},
            "limits": {},
        }

    def close(self) -> None:
        self.closes.append("vector")


class _RecordAndVectorStore(_RecordStore):
    """One object registered as both the record and the vector backend."""

    upsert = _VectorStore.upsert
    search = _VectorStore.search
    delete = _VectorStore.delete
    list_namespaces = _VectorStore.list_namespaces
    namespace_stats = _VectorStore.namespace_stats
    count = _VectorStore.count
    describe_backend = _VectorStore.describe_backend


def _engine_config(
    tmp_path: Path, *, vector_backend: str | None
) -> StorageEngineConfig:
    return StorageEngineConfig(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
        record_backend="record.counted",
        vector_backend=vector_backend,
    )


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        pytest.param("blob-store", ["record"], id="before-the-vector-store"),
        pytest.param(
            "emitter-start", ["vector", "record"], id="after-the-vector-store"
        ),
    ],
)
def test_engine_closes_the_backends_it_created_when_a_later_step_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str, expected: list[str]
) -> None:
    closes: list[str] = []
    stores: list[_RecordStore] = []
    injected = _Injected(step)
    registry = default_backend_registry()

    def record_factory(options: dict[str, Any]) -> _RecordStore:
        stores.append(_RecordStore(options["sqlite_path"], closes))
        return stores[-1]

    registry.register_record("record.counted", record_factory)
    registry.register_vector("vector.counted", lambda options: _VectorStore(closes))
    if step == "blob-store":
        registry.register_blob("blob.fs", _failing(injected))
    else:
        monkeypatch.setattr(
            storage_engine, "_start_pool_health_emitter", _failing(injected)
        )

    with pytest.raises(_Injected) as excinfo:
        StorageEngine.from_config(
            config=_engine_config(tmp_path, vector_backend="vector.counted"),
            registry=registry,
        )

    assert excinfo.value is injected
    # The same closes, in the same order, as StorageEngine.close().
    assert closes == expected
    [store] = stores
    assert connection_is_closed(store.connection)


def test_engine_closes_a_backend_that_is_both_record_and_vector_store_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closes: list[str] = []
    injected = _Injected("emitter start")
    shared = _RecordAndVectorStore(tmp_path / "storage.db", closes)
    registry = default_backend_registry()
    registry.register_record("record.counted", lambda options: shared)
    registry.register_vector("vector.shared", lambda options: shared)
    monkeypatch.setattr(
        storage_engine, "_start_pool_health_emitter", _failing(injected)
    )

    with pytest.raises(_Injected) as excinfo:
        StorageEngine.from_config(
            config=_engine_config(tmp_path, vector_backend="vector.shared"),
            registry=registry,
        )

    assert excinfo.value is injected
    assert closes == ["record"]
    assert connection_is_closed(shared.connection)


def test_engine_construction_keeps_its_error_when_a_backend_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closes: list[str] = []
    injected = _Injected("emitter start")

    class _FailingClose(_RecordStore):
        def close(self) -> None:
            super().close()
            raise OSError("record store close failed")

    registry = default_backend_registry()
    registry.register_record(
        "record.counted",
        lambda options: _FailingClose(options["sqlite_path"], closes),
    )
    monkeypatch.setattr(
        storage_engine, "_start_pool_health_emitter", _failing(injected)
    )

    with pytest.raises(_Injected) as excinfo:
        StorageEngine.from_config(
            config=_engine_config(tmp_path, vector_backend=None), registry=registry
        )

    assert excinfo.value is injected
    assert closes == ["record"]


def test_engine_construction_closes_the_record_store_when_the_vector_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closes: list[str] = []
    stores: list[_RecordStore] = []
    injected = _Injected("emitter start")

    class _FailingClose(_VectorStore):
        def close(self) -> None:
            super().close()
            raise OSError("vector store close failed")

    def record_factory(options: dict[str, Any]) -> _RecordStore:
        stores.append(_RecordStore(options["sqlite_path"], closes))
        return stores[-1]

    registry = default_backend_registry()
    registry.register_record("record.counted", record_factory)
    registry.register_vector("vector.counted", lambda options: _FailingClose(closes))
    monkeypatch.setattr(
        storage_engine, "_start_pool_health_emitter", _failing(injected)
    )

    with pytest.raises(_Injected) as excinfo:
        StorageEngine.from_config(
            config=_engine_config(tmp_path, vector_backend="vector.counted"),
            registry=registry,
        )

    assert excinfo.value is injected
    # The vector close raised, and the record store was still closed, once.
    assert closes == ["vector", "record"]
    [store] = stores
    assert connection_is_closed(store.connection)


@pytest.mark.parametrize(
    "engine_close_fails", [False, True], ids=["closes", "close-fails"]
)
def test_runtime_storage_closes_its_engine_when_migration_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine_close_fails: bool
) -> None:
    injected = _Injected("migration")
    engines: list[StorageEngine] = []
    engine_closes: list[StorageEngine] = []
    telemetry_closes: list[TelemetryService] = []
    from_config = StorageEngine.from_config
    engine_close = StorageEngine.close
    telemetry_close = TelemetryService.close_sync

    def recording_from_config(**kwargs: Any) -> StorageEngine:
        engines.append(from_config(**kwargs))
        return engines[-1]

    def counted_engine_close(self: StorageEngine) -> None:
        engine_closes.append(self)
        engine_close(self)
        if engine_close_fails:
            raise OSError("engine close failed")

    def counted_telemetry_close(self: TelemetryService) -> Any:
        telemetry_closes.append(self)
        return telemetry_close(self)

    monkeypatch.setattr(StorageEngine, "from_config", recording_from_config)
    monkeypatch.setattr(StorageEngine, "close", counted_engine_close)
    monkeypatch.setattr(TelemetryService, "close_sync", counted_telemetry_close)
    monkeypatch.setattr(storage_context, "migrate_database", _failing(injected))
    telemetry = TelemetryService(home_root=tmp_path)

    with pytest.raises(_Injected) as excinfo:
        build_runtime_storage(
            tmp_path / "state.db",
            env={},
            telemetry_hook=storage_hook.TelemetryServiceStorageHook(telemetry),
        )

    assert excinfo.value is injected
    [engine] = engines
    assert engine_closes == [engine]
    assert connection_is_closed(engine.record_store.connection)
    # The telemetry service is the caller's: its owner closes it, once.
    assert telemetry_closes == []
    telemetry.close_sync()
    assert telemetry_closes == [telemetry]
