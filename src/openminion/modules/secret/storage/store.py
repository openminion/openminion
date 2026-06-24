from __future__ import annotations

from pathlib import Path

from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from openminion.modules.storage.record_store import RecordStore
from .base import SecretStore
from .migrations import list_migrations

_SQLITE_SECRETS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS secrets (
    key TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'default',
    value TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (key, namespace)
)
"""

_POSTGRES_SECRETS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS secrets (
    key TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'default',
    value TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (key, namespace)
)
"""

_UPSERT_SECRET_SQL = """
INSERT INTO secrets (key, namespace, value, created_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(key, namespace) DO UPDATE SET
    value = excluded.value,
    updated_at = excluded.updated_at
"""


class _SecretStoreOps(SecretStore):
    def close(self) -> None:
        super().close()

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__

    def upsert(
        self,
        *,
        key: str,
        namespace: str,
        value: str,
        created_at: float,
        updated_at: float,
    ) -> None:
        self._record_store.execute_count(
            _UPSERT_SECRET_SQL,
            (key, namespace, value, created_at, updated_at),
        )

    def fetch_value(self, *, key: str, namespace: str) -> str | None:
        rows = self._record_store.query_rows(
            "secrets",
            where={"key": key, "namespace": namespace},
            limit=1,
        )
        if not rows:
            return None
        return str(rows[0]["value"])

    def delete(self, *, key: str, namespace: str) -> None:
        self._record_store.delete_rows(
            "secrets",
            where={"key": key, "namespace": namespace},
        )

    def list_keys(self, *, namespace: str) -> list[str]:
        rows = self._record_store.query_rows(
            "secrets",
            where={"namespace": namespace},
            order="key",
        )
        return [str(row["key"]) for row in rows]


class SQLiteSecretStore(_SecretStoreOps, BaseModuleSQLiteStore):
    """SQLite-backed secret store (module-owned schema + SQL)."""

    def __init__(
        self,
        sqlite_path: str | Path | None,
        *,
        record_store: RecordStore | None = None,
        wal: bool = True,
    ) -> None:
        super().__init__(sqlite_path, wal=wal, record_store=record_store)

    def _init_schema(self) -> None:
        with self._lock:
            self._record_store.execute_count(_SQLITE_SECRETS_TABLE_SQL)


class PostgresSecretStore(_SecretStoreOps, BaseModuleStore):
    """Backend-neutral secret store used for Postgres pilot coverage."""

    def __init__(self, *, record_store: RecordStore) -> None:
        super().__init__(record_store=record_store)

    def _init_schema(self) -> None:
        with self._lock:
            self._record_store.execute_count(_POSTGRES_SECRETS_TABLE_SQL)


__all__ = ["PostgresSecretStore", "SQLiteSecretStore"]
