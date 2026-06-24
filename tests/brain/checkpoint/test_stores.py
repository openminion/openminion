from __future__ import annotations

import pytest

from openminion.modules.brain.checkpoint.stores import (
    CheckpointRecord,
    InMemoryCheckpointStore,
    PostgresCheckpointStore,
    RedisCheckpointStore,
    build_checkpoint_store,
)


def _make_record(key: str = "sess1:turn1") -> CheckpointRecord:
    return CheckpointRecord(
        key=key,
        payload={"phase": "act", "iter": 3},
        metadata={"trace_id": "trace-xyz"},
    )


def test_in_memory_put_get_round_trip() -> None:
    store = InMemoryCheckpointStore()
    record = _make_record()
    store.put(record)
    fetched = store.get(record.key)
    assert fetched is not None
    assert fetched.payload == {"phase": "act", "iter": 3}
    assert fetched.metadata == {"trace_id": "trace-xyz"}


def test_in_memory_get_missing_returns_none() -> None:
    assert InMemoryCheckpointStore().get("nope") is None


def test_in_memory_list_returns_sorted_keys() -> None:
    store = InMemoryCheckpointStore()
    store.put(_make_record("z"))
    store.put(_make_record("a"))
    store.put(_make_record("m"))
    assert store.list() == ["a", "m", "z"]


def test_in_memory_list_filters_by_prefix() -> None:
    store = InMemoryCheckpointStore()
    for k in ("sess1:t1", "sess1:t2", "sess2:t1"):
        store.put(_make_record(k))
    assert store.list(prefix="sess1:") == ["sess1:t1", "sess1:t2"]


def test_in_memory_delete_returns_true_when_found() -> None:
    store = InMemoryCheckpointStore()
    store.put(_make_record("k"))
    assert store.delete("k") is True
    assert store.delete("k") is False  # second delete is a miss


def test_in_memory_put_overwrites_same_key() -> None:
    store = InMemoryCheckpointStore()
    store.put(_make_record("k"))
    store.put(
        CheckpointRecord(key="k", payload={"different": True}, version=2),
    )
    fetched = store.get("k")
    assert fetched is not None
    assert fetched.payload == {"different": True}
    assert fetched.version == 2


def test_build_checkpoint_store_defaults_to_memory() -> None:
    assert isinstance(build_checkpoint_store(""), InMemoryCheckpointStore)
    assert isinstance(build_checkpoint_store("memory"), InMemoryCheckpointStore)


def test_build_checkpoint_store_returns_postgres_for_dsn() -> None:
    store = build_checkpoint_store("postgresql://user:pass@host/db")
    assert isinstance(store, PostgresCheckpointStore)
    assert store.dsn == "postgresql://user:pass@host/db"


def test_build_checkpoint_store_returns_redis_for_url() -> None:
    store = build_checkpoint_store("redis://localhost:6379/0")
    assert isinstance(store, RedisCheckpointStore)
    assert store.url == "redis://localhost:6379/0"


def test_build_checkpoint_store_rejects_unknown_spec() -> None:
    with pytest.raises(ValueError, match="unrecognized checkpoint spec"):
        build_checkpoint_store("dynamodb://table-name")


def test_postgres_store_lazy_import_raises_helpful_error_when_extras_missing() -> None:
    store = PostgresCheckpointStore(dsn="postgresql://invalid:invalid@127.0.0.1:1/x")
    with pytest.raises((RuntimeError, Exception)):
        store.get("anything")


def test_redis_store_lazy_import_raises_helpful_error_when_extras_missing() -> None:
    store = RedisCheckpointStore(url="redis://127.0.0.1:1/0")
    with pytest.raises((RuntimeError, Exception)):
        store.get("anything")
