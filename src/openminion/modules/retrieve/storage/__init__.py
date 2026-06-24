from __future__ import annotations

from pathlib import Path

from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig

from .base import RetrieveStore
from .store import PostgresRetrieveStore, SQLiteRetrieveStore


def build_retrieve_store(
    *,
    config: StorageEngineConfig,
    database_path: str | Path,
) -> RetrieveStore:
    backend = str(config.record_backend).strip()
    raw_db_path = str(database_path).strip()
    if backend == "record.sqlite":
        return SQLiteRetrieveStore(
            raw_db_path if raw_db_path == ":memory:" else database_path
        )

    if backend != "record.postgres":
        raise ValueError(
            f"Unsupported retrieve record backend: {config.record_backend!r}"
        )

    engine = StorageEngine.from_config(config=config)
    return PostgresRetrieveStore(record_store=engine.record_store)


__all__ = (
    "PostgresRetrieveStore",
    "RetrieveStore",
    "SQLiteRetrieveStore",
    "build_retrieve_store",
)
