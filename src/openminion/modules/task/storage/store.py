from pathlib import Path

from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from openminion.modules.storage.record_store import RecordStore
from .base import TaskStore
from .migrations import list_migrations
from openminion.modules.storage.migrations.task_tables import migrate_v1_to_v2


def ensure_schema(store: RecordStore) -> None:
    """Initialize the database schema for task persistence."""
    migrate_v1_to_v2(store)


class SQLiteTaskStore(BaseModuleSQLiteStore, TaskStore):
    """SQLite-backed store for openminion-task."""

    def __init__(self, sqlite_path: str | Path, *, wal: bool = True) -> None:
        super().__init__(sqlite_path, wal=wal)
        self.record_store = self._record_store

    def _init_schema(self) -> None:
        ensure_schema(self._record_store)

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__


class PostgresTaskStore(BaseModuleStore, TaskStore):
    """Postgres-backed store for openminion-task."""

    def __init__(self, *, record_store: RecordStore) -> None:
        super().__init__(record_store=record_store)
        self.record_store = self._record_store

    def _init_schema(self) -> None:
        ensure_schema(self._record_store)

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__


__all__ = ["PostgresTaskStore", "SQLiteTaskStore", "ensure_schema"]
