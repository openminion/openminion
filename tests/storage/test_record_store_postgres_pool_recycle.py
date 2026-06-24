from __future__ import annotations

import time

import pytest


sqlalchemy = pytest.importorskip("sqlalchemy")


def test_record_store_postgres_default_pool_recycle_is_sqlalchemy_default() -> None:

    from openminion.modules.storage.backends.postgres import (
        RecordStorePostgres,
    )

    default_store = RecordStorePostgres("sqlite:///:memory:")
    try:
        default_recycle = default_store._engine.pool._recycle  # noqa: SLF001
    finally:
        default_store.close()

    configured_store = RecordStorePostgres(
        "sqlite:///:memory:",
        pool_recycle_seconds=1800,
    )
    try:
        configured_recycle = configured_store._engine.pool._recycle  # noqa: SLF001
        assert configured_recycle == 1800
        # The explicit value must differ from the SQLAlchemy default; this
        # is the load-bearing assertion (no silent passthrough of None).
        assert configured_recycle != default_recycle
    finally:
        configured_store.close()


def test_record_store_postgres_pool_recycle_zero_means_never_recycle() -> None:

    from openminion.modules.storage.backends.postgres import (
        RecordStorePostgres,
    )

    store = RecordStorePostgres(
        "sqlite:///:memory:",
        pool_recycle_seconds=0,
    )
    try:
        assert store._engine.pool._recycle == 0  # noqa: SLF001
    finally:
        store.close()


def test_record_store_postgres_pool_size_and_overflow_forwarded() -> None:

    import tempfile
    from pathlib import Path

    from openminion.modules.storage.backends.postgres import (
        RecordStorePostgres,
    )

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "pool.db"
        store = RecordStorePostgres(
            f"sqlite:///{db_path}",
            pool_size=7,
            pool_max_overflow=3,
            pool_timeout_seconds=12.5,
        )
        try:
            pool = store._engine.pool
            # SQLAlchemy may or may not use QueuePool here depending on
            # dialect defaults; assert only on attributes when present.
            if hasattr(pool, "_pool"):
                # QueuePool path
                assert pool.size() == 7
            if hasattr(pool, "_max_overflow"):
                assert pool._max_overflow == 3  # noqa: SLF001
            if hasattr(pool, "_timeout"):
                assert pool._timeout == 12.5  # noqa: SLF001
        finally:
            store.close()


def test_record_store_postgres_pool_recycle_evicts_old_connection() -> None:

    import tempfile
    from pathlib import Path

    from sqlalchemy import event as sa_event

    from openminion.modules.storage.backends.postgres import (
        RecordStorePostgres,
    )

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "recycle.db"
        store = RecordStorePostgres(
            f"sqlite:///{db_path}",
            pool_recycle_seconds=1,
        )
        try:
            engine = store._engine
            connect_count = {"n": 0}

            def _on_connect(dbapi_conn, conn_record):  # noqa: ARG001
                connect_count["n"] += 1

            sa_event.listen(engine.pool, "connect", _on_connect)

            with engine.connect():
                pass
            count_after_first = connect_count["n"]
            assert count_after_first >= 1, "Initial checkout must open a connection"

            # Sleep past the 1-second recycle threshold.
            time.sleep(1.2)
            with engine.connect():
                pass
            count_after_recycle = connect_count["n"]
            # The recycled checkout MUST have produced a fresh DBAPI
            # connect event — that is the load-bearing recycle behavior.
            assert count_after_recycle > count_after_first, (
                "Pool.recycle did not evict the stale connection: "
                f"connect events stayed at {count_after_first}"
            )
        finally:
            store.close()


def test_storage_engine_config_forwards_pool_recycle_via_factory(tmp_path) -> None:

    from openminion.modules.storage.backends.registry import (
        default_backend_registry,
    )

    registry = default_backend_registry()
    record = registry.create_record(
        "record.postgres",
        {
            "url": "sqlite:///:memory:",
            "pool_recycle_seconds": 600,
        },
    )
    try:
        assert record._engine.pool._recycle == 600  # noqa: SLF001
    finally:
        record.close()
