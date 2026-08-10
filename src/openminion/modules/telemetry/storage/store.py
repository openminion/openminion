import json
from pathlib import Path
from typing import Any

from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from openminion.modules.storage.record_store import RecordStore
from openminion.modules.telemetry.schemas import (
    TELEMETRY_EVENT_SCHEMA_V1,
    TelemetryEvent,
)
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
            schema_version TEXT NOT NULL,
            event_id TEXT NOT NULL,
            trace_key TEXT,
            invocation_id TEXT,
            execution_id TEXT,
            agent_id TEXT,
            mode TEXT,
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
    for column_name, ddl_tail in (
        ("schema_version", "TEXT"),
        ("event_id", "TEXT"),
        ("trace_key", "TEXT"),
        ("invocation_id", "TEXT"),
        ("execution_id", "TEXT"),
        ("agent_id", "TEXT"),
        ("mode", "TEXT"),
    ):
        _ensure_store_column(
            record_store,
            table_name="events",
            column_name=column_name,
            ddl_tail=ddl_tail,
        )
    record_store.execute_count(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_event_id ON events(event_id)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_events_invocation_time "
        "ON events(invocation_id, timestamp)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_events_execution_time "
        "ON events(execution_id, timestamp)"
    )


def _table_columns(record_store: RecordStore, table_name: str) -> set[str]:
    if bool(record_store.capabilities().get("raw_sql")):
        rows = record_store.query_dicts(f"PRAGMA table_info({table_name})")
    else:
        rows = record_store.query_dicts(
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ?
            """,
            (table_name,),
        )
    return {str(row["name"]) for row in rows}


def _ensure_store_column(
    record_store: RecordStore,
    *,
    table_name: str,
    column_name: str,
    ddl_tail: str,
) -> None:
    columns = _table_columns(record_store, table_name)
    if not columns or column_name in columns:
        return
    record_store.execute_count(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_tail}"
    )


class _TelemetryStoreMixin(TelemetryStore):
    _record_store: RecordStore

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__

    def insert_event(self, event: TelemetryEvent) -> None:
        self._record_store.insert(
            "events",
            {
                "session_id": event.session_id,
                "turn_id": event.turn_id,
                "event_type": event.event_type,
                "timestamp": float(event.timestamp),
                "schema_version": event.schema_version,
                "event_id": event.event_id,
                "trace_key": event.trace_key,
                "invocation_id": event.invocation_id,
                "execution_id": event.execution_id,
                "agent_id": event.agent_id,
                "mode": event.mode,
                "data": json.dumps(event.data),
            },
        )

    @staticmethod
    def _row_to_event(row: dict[str, Any]) -> TelemetryEvent:
        data = json.loads(str(row["data"]))
        return TelemetryEvent(
            session_id=str(row["session_id"]),
            turn_id=str(row["turn_id"]),
            event_type=str(row["event_type"]),
            timestamp=float(row["timestamp"]),
            data=data,
            mode=str(row.get("mode") or data.get("mode") or "").strip() or None,
            schema_version=str(row.get("schema_version") or TELEMETRY_EVENT_SCHEMA_V1),
            event_id=str(row.get("event_id") or ""),
            trace_key=str(row.get("trace_key") or "").strip() or None,
            invocation_id=str(row.get("invocation_id") or "").strip() or None,
            execution_id=str(row.get("execution_id") or "").strip() or None,
            agent_id=str(row.get("agent_id") or "").strip() or None,
        )

    def _fetch_events(self, where: dict[str, Any]) -> list[TelemetryEvent]:
        rows = self._record_store.query_rows(
            "events",
            where=where,
            order="timestamp, id",
        )
        return [self._row_to_event(row) for row in rows]

    def fetch_session_events(self, session_id: str) -> list[TelemetryEvent]:
        return self._fetch_events({"session_id": session_id})

    def fetch_invocation_events(self, invocation_id: str) -> list[TelemetryEvent]:
        return self._fetch_events({"invocation_id": invocation_id})

    def fetch_execution_events(self, execution_id: str) -> list[TelemetryEvent]:
        return self._fetch_events({"execution_id": execution_id})

    def fetch_events(self) -> list[TelemetryEvent]:
        return self._fetch_events({})

    def delete_invocation_events(self, invocation_id: str) -> int:
        return int(
            self._record_store.delete_rows("events", {"invocation_id": invocation_id})
        )


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
