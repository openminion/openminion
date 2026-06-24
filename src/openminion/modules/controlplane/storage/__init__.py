from pathlib import Path

from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig

from .base import ControlplaneStore
from .inbox_outbox import InboxOutboxStore
from .principals import PrincipalsStore
from .store import PostgresControlPlaneStore, SQLiteControlPlaneStore


def build_controlplane_store(
    *,
    config: StorageEngineConfig,
    database_path: str | Path,
) -> ControlplaneStore:
    backend = str(config.record_backend).strip()
    raw_db_path = str(database_path).strip()
    if backend == "record.sqlite":
        sqlite_path = raw_db_path if raw_db_path == ":memory:" else database_path
        return SQLiteControlPlaneStore(sqlite_path)

    if backend == "record.postgres":
        engine = StorageEngine.from_config(config=config)
        return PostgresControlPlaneStore(record_store=engine.record_store)

    raise ValueError(
        f"Unsupported controlplane record backend: {config.record_backend!r}"
    )


__all__ = [
    "ControlplaneStore",
    "SQLiteControlPlaneStore",
    "PostgresControlPlaneStore",
    "InboxOutboxStore",
    "PrincipalsStore",
    "build_controlplane_store",
]
