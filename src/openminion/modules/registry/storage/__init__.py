from __future__ import annotations

from pathlib import Path

from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig

from .base import RegistryStore
from .memory import InMemoryRegistryStore
from .store import PostgresRegistryStore, SQLiteRegistryStore


def build_registry_store(
    *,
    config: StorageEngineConfig,
    database_path: str | Path,
) -> RegistryStore:
    backend = str(config.record_backend).strip()
    raw_db_path = str(database_path).strip()
    if backend == "record.sqlite":
        return SQLiteRegistryStore(
            raw_db_path if raw_db_path == ":memory:" else database_path
        )

    if backend != "record.postgres":
        raise ValueError(
            f"Unsupported registry record backend: {config.record_backend!r}"
        )

    engine = StorageEngine.from_config(config=config)
    return PostgresRegistryStore(record_store=engine.record_store)


__all__ = (
    "InMemoryRegistryStore",
    "PostgresRegistryStore",
    "RegistryStore",
    "SQLiteRegistryStore",
    "build_registry_store",
)
