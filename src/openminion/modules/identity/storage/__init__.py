from pathlib import Path

from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig

from .base import CachedSnippet, IdentityStore, StoredProfile
from .memory import InMemoryIdentityStore
from .store import PostgresIdentityStore, SQLiteIdentityStore


def build_identity_store(
    *,
    config: StorageEngineConfig,
    database_path: str | Path,
) -> IdentityStore:
    backend = str(config.record_backend).strip()
    raw_db_path = str(database_path).strip()
    if backend == "record.sqlite":
        return SQLiteIdentityStore(
            raw_db_path if raw_db_path == ":memory:" else database_path
        )

    if backend != "record.postgres":
        raise ValueError(
            f"Unsupported identity record backend: {config.record_backend!r}"
        )

    engine = StorageEngine.from_config(config=config)
    return PostgresIdentityStore(record_store=engine.record_store)


__all__ = [
    "CachedSnippet",
    "IdentityStore",
    "InMemoryIdentityStore",
    "PostgresIdentityStore",
    "SQLiteIdentityStore",
    "StoredProfile",
    "build_identity_store",
]
