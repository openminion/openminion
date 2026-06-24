import json
from pathlib import Path
from typing import Any

from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from openminion.modules.storage.record_store import RecordStore
from .base import TelemetryStore
from .migrations import list_migrations


def _create_events_schema(
    record_store: RecordStore,
    *,
    timestamp_type: str = "REAL",
    id_column_sql: str = "INTEGER PRIMARY KEY AUTOINCREMENT",
) -> None:
    record_store.execute_count(
        f"""
        CREATE TABLE IF NOT EXISTS events (
            id {id_column_sql},
            session_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            timestamp {timestamp_type} NOT NULL,
            data TEXT NOT NULL
        )
        """
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_session ON events(session_id)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_turn ON events(session_id, turn_id)"
    )


class _TelemetryStoreMixin(TelemetryStore):
    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__

    def insert_event(
        self,
        *,
        session_id: str,
        turn_id: str,
        event_type: str,
        timestamp: float,
        data: dict[str, Any],
    ) -> None:
        self._record_store.insert(
            "events",
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "event_type": event_type,
                "timestamp": float(timestamp),
                "data": json.dumps(data),
            },
        )

    def fetch_session_events(
        self, session_id: str
    ) -> list[tuple[str, str, float, str]]:
        rows = self._record_store.query_rows(
            "events",
            where={"session_id": session_id},
            order="timestamp, id",
        )
        return [
            (
                str(row["turn_id"]),
                str(row["event_type"]),
                float(row["timestamp"]),
                str(row["data"]),
            )
            for row in rows
        ]


class SQLiteTelemetryStore(BaseModuleSQLiteStore, _TelemetryStoreMixin):
    """SQLite-backed telemetry store (module-owned schema + SQL)."""

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
            _create_events_schema(self._record_store)

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__


class PostgresTelemetryStore(BaseModuleStore, _TelemetryStoreMixin):
    """Postgres-backed telemetry store."""

    def __init__(self, *, record_store: RecordStore) -> None:
        super().__init__(record_store=record_store)

    def _init_schema(self) -> None:
        with self._lock:
            _create_events_schema(
                self._record_store,
                timestamp_type="DOUBLE PRECISION",
                id_column_sql="SERIAL PRIMARY KEY",
            )

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__


__all__ = ["PostgresTelemetryStore", "SQLiteTelemetryStore"]
