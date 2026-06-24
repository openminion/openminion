from pathlib import Path

from openminion.modules.storage.engine import StorageEngine, StorageEngineConfig

from .base import TaskStore
from .store import PostgresTaskStore, SQLiteTaskStore, ensure_schema


def build_task_store(
    *,
    config: StorageEngineConfig,
    database_path: str | Path,
) -> TaskStore:
    raw_db_path = str(database_path).strip()
    if str(config.record_backend).strip() == "record.sqlite":
        return SQLiteTaskStore(
            raw_db_path if raw_db_path == ":memory:" else database_path
        )

    if str(config.record_backend).strip() != "record.postgres":
        raise ValueError(f"Unsupported task record backend: {config.record_backend!r}")

    engine = StorageEngine.from_config(config=config)
    return PostgresTaskStore(record_store=engine.record_store)


__all__ = (
    "PostgresTaskStore",
    "SQLiteTaskStore",
    "TaskStore",
    "build_task_store",
    "ensure_schema",
)
