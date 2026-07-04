import os
import pytest
import sys

from openminion.base.version import OPENMINION_VERSION
from openminion.modules.storage import (
    BackendRegistry,
    StorageEngine,
    StorageEngineConfig,
    default_backend_registry,
)
from openminion.modules.storage.backends.blob_store import BlobStoreFS
from openminion.modules.storage.record_store import RecordStoreSQLite

pytestmark = pytest.mark.postgres


class MarkerRecordStore(RecordStoreSQLite):
    pass


class MarkerBlobStore(BlobStoreFS):
    pass


class MarkerVectorStore:
    contract_version = "v1"

    def __init__(self, options):
        self.options = dict(options)
        self._namespaces: set[str] = set()
        self.closed = False

    def upsert(self, vectors, metadata, ids, namespace=None):
        if namespace:
            self._namespaces.add(str(namespace))

    def search(self, query_vector, top_k=10, filters=None, namespace=None):
        return []

    def delete(self, ids, namespace=None):
        return True

    def list_namespaces(self):
        return sorted(self._namespaces)

    def namespace_stats(self, namespace):
        return {"namespace": namespace, "count": 0}

    def count(self, namespace=None):
        return 0

    def healthcheck(self):
        return {"ok": True}

    def describe_backend(self):
        return {
            "backend_id": "vector.marker",
            "version": OPENMINION_VERSION,
            "planes_supported": {"vector"},
            "capabilities": {},
            "limits": {},
        }

    def close(self):
        self.closed = True


def test_engine_from_config_uses_default_backends(tmp_path):
    config = StorageEngineConfig(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
        vector_backend="vector.noop",
    )
    engine = StorageEngine.from_config(config=config)
    try:
        assert isinstance(engine.record_store, RecordStoreSQLite)
        assert isinstance(engine.blob_store, BlobStoreFS)
        assert engine.vector_store is not None
    finally:
        engine.close()


def test_default_registry_includes_zvec_backend():
    registry = default_backend_registry()
    assert "vector.zvec" in registry.list_vector_backends()


def test_default_registry_includes_postgres_record_backend():
    registry = default_backend_registry()
    assert "record.postgres" in registry.list_record_backends()


def test_postgres_backend_requires_url():
    registry = default_backend_registry()
    with pytest.raises(ValueError, match="url is required"):
        registry.create_record("record.postgres", {})


def test_default_sqlite_engine_does_not_import_postgres_backend(tmp_path):
    sys.modules.pop("openminion.modules.storage.backends.postgres", None)
    config = StorageEngineConfig(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
    )
    engine = StorageEngine.from_config(config=config)
    try:
        assert isinstance(engine.record_store, RecordStoreSQLite)
        assert "openminion.modules.storage.backends.postgres" not in sys.modules
    finally:
        engine.close()


@pytest.mark.postgres
def test_engine_from_config_supports_postgres_record_backend(tmp_path):
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    config = StorageEngineConfig(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
        record_backend="record.postgres",
        record_backend_options={"url": postgres_url},
    )
    engine = StorageEngine.from_config(config=config)
    try:
        assert engine.record_store.__class__.__name__ == "RecordStorePostgres"
    finally:
        engine.close()


def test_engine_from_config_supports_zvec_vector_backend(tmp_path):
    config = StorageEngineConfig(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
        vector_backend="vector.zvec",
        vector_backend_options={"dimension": 3},
    )
    engine = StorageEngine.from_config(config=config)
    try:
        module = engine.module("memctl")
        module.vector_upsert(
            vectors=[[1.0, 0.0, 0.0], [0.1, 0.9, 0.0]],
            metadata=[{"kind": "a"}, {"kind": "b"}],
            ids=["v1", "v2"],
        )
        hits = module.vector_search(query_vector=[1.0, 0.0, 0.0], top_k=2)
        assert len(hits) == 2
        assert hits[0]["id"] == "v1"
        assert module.vector_count() == 2
        assert engine.vector_store is not None
        assert engine.vector_store.describe_backend().backend_id == "vector.zvec"
    finally:
        engine.close()


def test_engine_from_config_supports_custom_backend_binding(tmp_path):
    registry = BackendRegistry()
    seen: dict[str, dict] = {}

    def record_factory(options):
        seen["record"] = dict(options)
        return MarkerRecordStore(
            options["sqlite_path"],
            wal=bool(options.get("wal", True)),
            synchronous=str(options.get("synchronous", "NORMAL")),
            busy_timeout_ms=int(options.get("busy_timeout_ms", 5000)),
            autocheckpoint_pages=int(options.get("autocheckpoint_pages", 1000)),
        )

    def blob_factory(options):
        seen["blob"] = dict(options)
        return MarkerBlobStore(options["root_dir"])

    def vector_factory(options):
        seen["vector"] = dict(options)
        return MarkerVectorStore(options)

    registry.register_record("record.marker", record_factory)
    registry.register_blob("blob.marker", blob_factory)
    registry.register_vector("vector.marker", vector_factory)

    config = StorageEngineConfig(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
        record_backend="record.marker",
        blob_backend="blob.marker",
        vector_backend="vector.marker",
        vector_backend_options={"dimension": 384},
    )

    engine = StorageEngine.from_config(config=config, registry=registry)
    try:
        assert isinstance(engine.record_store, MarkerRecordStore)
        assert isinstance(engine.blob_store, MarkerBlobStore)
        assert isinstance(engine.vector_store, MarkerVectorStore)
        assert seen["record"]["sqlite_path"] == config.sqlite_path
        assert seen["blob"]["root_dir"] == config.root_dir
        assert seen["vector"]["dimension"] == 384
    finally:
        marker = engine.vector_store
        engine.close()
    assert isinstance(marker, MarkerVectorStore)
    assert marker.closed is True


def test_engine_from_config_raises_on_unknown_backend(tmp_path):
    config = StorageEngineConfig(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
        record_backend="record.missing",
    )
    with pytest.raises(KeyError):
        StorageEngine.from_config(config=config)


def test_engine_from_config_raises_on_unknown_vector_backend(tmp_path):
    config = StorageEngineConfig(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
        vector_backend="vector.missing",
    )
    with pytest.raises(KeyError):
        StorageEngine.from_config(config=config)


def test_engine_from_config_rejects_nonconformant_record_backend(tmp_path):
    registry = default_backend_registry()
    registry.register_record("record.bad", lambda options: object())

    config = StorageEngineConfig(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
        record_backend="record.bad",
    )

    with pytest.raises(TypeError):
        StorageEngine.from_config(config=config, registry=registry)


def test_engine_from_config_rejects_nonconformant_vector_backend(tmp_path):
    registry = default_backend_registry()
    registry.register_vector("vector.bad", lambda options: object())

    config = StorageEngineConfig(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
        vector_backend="vector.bad",
    )

    with pytest.raises(TypeError):
        StorageEngine.from_config(config=config, registry=registry)
