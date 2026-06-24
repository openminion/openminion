import sqlite3

from openminion.modules.storage.backends.blob_store import BlobStoreFS
from openminion.modules.storage.engine import StorageEngine
from openminion.modules.storage.backends.hybrid_store import HybridStore
from openminion.modules.storage.record_store import RecordStoreSQLite


def test_engine_module_write_event_namespaced(tmp_path):
    engine = StorageEngine.from_paths(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
    )
    try:
        module = engine.module("skillctl")
        ref = module.write_event(
            {
                "session_id": "sess-1",
                "type": "skill.ingest",
                "payload": {"name": "hello"},
            }
        )

        assert ref.persisted == "sqlite"
        assert ref.namespace == "skillctl"

        rows = engine.record_store.query_dicts(
            "SELECT namespace, event_id FROM core_events WHERE event_id = ?",
            (ref.event_id,),
        )
        assert rows
        assert rows[0]["namespace"] == "skillctl"

        events = module.list_events("sess-1")
        assert len(events) == 1
        assert events[0]["namespace"] == "skillctl"
    finally:
        engine.close()


def test_hybrid_sidecar_reindex_namespaced(tmp_path):
    record = RecordStoreSQLite(tmp_path / "storage.db", wal=True)
    blob = BlobStoreFS(tmp_path / "blob")
    hybrid = HybridStore(
        record_store=record, blob_store=blob, fallback_root=tmp_path / "fallback"
    )

    original_execute_count = record.execute_count

    def fail_core_event_insert(sql, params=None):
        if "INSERT INTO core_events" in sql:
            raise sqlite3.OperationalError("sqlite unavailable")
        return original_execute_count(sql, params)

    record.execute_count = fail_core_event_insert  # type: ignore[method-assign]
    sidecar_ref = hybrid.write_event(
        {
            "session_id": "sess-2",
            "type": "tool.call.completed",
            "payload": {"ok": True},
        },
        namespace="sessctl",
    )
    record.execute_count = original_execute_count  # type: ignore[method-assign]

    assert sidecar_ref.persisted == "sidecar"
    assert sidecar_ref.sidecar_path is not None
    assert "/modules/sessctl/sessions/sess-2/events.jsonl" in sidecar_ref.sidecar_path

    report = hybrid.reindex(namespace="sessctl")
    assert report.inserted == 1
    assert report.failed == 0

    rows = record.query_dicts(
        "SELECT namespace FROM core_events WHERE event_id = ?",
        (sidecar_ref.event_id,),
    )
    assert rows
    assert rows[0]["namespace"] == "sessctl"

    ingest = record.query_dicts(
        "SELECT namespace FROM sidecar_ingest_log WHERE source_path = ?",
        (str(sidecar_ref.sidecar_path),),
    )
    assert ingest
    assert ingest[0]["namespace"] == "sessctl"

    close_fn = getattr(record, "close", None)
    if callable(close_fn):
        close_fn()


def test_engine_module_vector_ops_namespaced(tmp_path):
    engine = StorageEngine.from_paths(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
        vector_backend="vector.zvec",
        vector_backend_options={"dimension": 2},
    )
    try:
        module_a = engine.module("moda")
        module_b = engine.module("modb")
        module_a.vector_upsert(
            vectors=[[1.0, 0.0]],
            metadata=[{"owner": "a"}],
            ids=["va"],
        )
        module_b.vector_upsert(
            vectors=[[0.0, 1.0]],
            metadata=[{"owner": "b"}],
            ids=["vb"],
        )

        hits_a = module_a.vector_search(query_vector=[1.0, 0.0], top_k=5)
        hits_b = module_b.vector_search(query_vector=[0.0, 1.0], top_k=5)

        assert len(hits_a) == 1
        assert hits_a[0]["id"] == "va"
        assert hits_a[0]["namespace"] == "moda"
        assert len(hits_b) == 1
        assert hits_b[0]["id"] == "vb"
        assert hits_b[0]["namespace"] == "modb"
    finally:
        engine.close()


def test_engine_module_exposes_sqlite_and_zvec_planes(tmp_path):
    engine = StorageEngine.from_paths(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
        vector_backend="vector.zvec",
        vector_backend_options={"dimension": 3},
    )
    try:
        module = engine.module("memory")
        module.sql_execute(
            """
            CREATE TABLE IF NOT EXISTS memory_records (
                id TEXT PRIMARY KEY,
                summary TEXT NOT NULL
            )
            """
        )
        module.sql_execute(
            "INSERT INTO memory_records(id, summary) VALUES (?, ?)",
            ("m1", "first memory"),
        )
        rows = module.sql_query(
            "SELECT id, summary FROM memory_records WHERE id = ?", ("m1",)
        )
        assert len(rows) == 1
        assert rows[0]["id"] == "m1"
        assert rows[0]["summary"] == "first memory"

        module.vector_upsert(
            vectors=[[0.2, 0.8, 0.0]],
            metadata=[{"scope": "global"}],
            ids=["m1"],
        )
        hits = module.vector_search(query_vector=[0.2, 0.8, 0.0], top_k=1)
        assert len(hits) == 1
        assert hits[0]["id"] == "m1"
        assert hits[0]["namespace"] == "memory"
    finally:
        engine.close()
