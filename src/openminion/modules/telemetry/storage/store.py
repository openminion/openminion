import json
import math
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
from .base import (
    TelemetryEventConflictError,
    TelemetryEventPageRow,
    TelemetryStore,
)
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
    for name, columns in (
        (
            "idx_events_type_time_invocation_id",
            "event_type, timestamp, invocation_id, id",
        ),
        ("idx_events_invocation_time_id", "invocation_id, timestamp, id"),
        (
            "idx_events_session_turn_invocation_id",
            "session_id, turn_id, invocation_id, id",
        ),
    ):
        record_store.execute_count(
            f"CREATE INDEX IF NOT EXISTS {name} ON events({columns})"
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

    @staticmethod
    def _event_row(event: TelemetryEvent) -> dict[str, Any]:
        return {
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
        }

    @staticmethod
    def _duplicate_facts(event: TelemetryEvent) -> tuple[Any, ...]:
        data = dict(event.data)
        if event.event_type.startswith("agent.invocation."):
            data.pop("_telemetry_policy", None)
        return (
            event.session_id,
            event.turn_id,
            event.event_type,
            float(event.timestamp).hex(),
            event.mode,
            event.trace_key,
            event.invocation_id,
            event.execution_id,
            event.agent_id,
            data,
        )

    def insert_event(self, event: TelemetryEvent) -> None:
        self._record_store.insert("events", self._event_row(event))

    def insert_event_if_absent(self, event: TelemetryEvent) -> bool:
        event_id = str(event.event_id or "").strip()
        if not event_id:
            raise ValueError("event_id must be non-empty")
        if self._record_store.insert_if_absent(
            "events",
            self._event_row(event),
            conflict_columns=("event_id",),
        ):
            return True
        rows = self._record_store.query_rows(
            "events",
            where={"event_id": event_id},
            limit=2,
        )
        if len(rows) != 1:
            raise RuntimeError(f"event_id lookup returned {len(rows)} rows")
        if self._duplicate_facts(self._row_to_event(rows[0])) != self._duplicate_facts(
            event
        ):
            raise TelemetryEventConflictError(
                f"event_id {event_id!r} already owns different structural facts"
            )
        return False

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

    def event_high_water(self, *, invocation_id: str | None = None) -> int:
        query = "SELECT COALESCE(MAX(id), 0) AS high_water FROM events"
        params: list[Any] = []
        if invocation_id:
            query += " WHERE invocation_id = ?"
            params.append(invocation_id)
        row = self._record_store.query_dicts(query, params)[0]
        return int(row["high_water"] or 0)

    def fetch_event_page(
        self,
        *,
        high_water: int,
        limit: int,
        before_timestamp: float | None = None,
        before_id: int | None = None,
        invocation_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        event_types: tuple[str, ...] = (),
    ) -> list[TelemetryEventPageRow]:
        safe_limit = int(limit)
        if safe_limit < 1 or safe_limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        clauses = ["id <= ?"]
        params: list[Any] = [max(0, int(high_water))]
        for column, value in (
            ("invocation_id", invocation_id),
            ("session_id", session_id),
            ("turn_id", turn_id),
        ):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        if event_types:
            clauses.append(
                "event_type IN (" + ", ".join("?" for _ in event_types) + ")"
            )
            params.extend(event_types)
        if before_timestamp is not None:
            if before_id is None:
                raise ValueError("before_id is required with before_timestamp")
            clauses.append("(timestamp < ? OR (timestamp = ? AND id < ?))")
            params.extend((before_timestamp, before_timestamp, int(before_id)))
        query = (
            "SELECT id, session_id, turn_id, event_type, "
            "timestamp AS raw_timestamp, "
            "CAST(timestamp AS DOUBLE PRECISION) AS timestamp, schema_version, "
            "event_id, trace_key, invocation_id, execution_id, agent_id, mode, data "
            "FROM events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY timestamp DESC, id DESC LIMIT ?"
        )
        params.append(safe_limit)
        return [
            TelemetryEventPageRow(
                row_id=int(row["id"]),
                event=self._row_to_event(row),
                timestamp_valid=isinstance(row["raw_timestamp"], (int, float))
                and not isinstance(row["raw_timestamp"], bool)
                and math.isfinite(float(row["raw_timestamp"])),
            )
            for row in self._record_store.query_dicts(query, params)
        ]

    def find_turn_invocation_ids(
        self,
        *,
        session_id: str,
        turn_id: str,
        high_water: int | None = None,
        limit: int = 2,
    ) -> list[str]:
        safe_limit = max(1, min(int(limit), 1000))
        clauses = [
            "session_id = ?",
            "turn_id = ?",
            "invocation_id IS NOT NULL",
        ]
        params: list[Any] = [session_id, turn_id]
        if high_water is not None:
            clauses.append("id <= ?")
            params.append(max(0, int(high_water)))
        params.append(safe_limit)
        rows = self._record_store.query_dicts(
            f"""
            SELECT invocation_id, MAX(timestamp) AS latest_timestamp, MAX(id) AS latest_id
            FROM events
            WHERE {" AND ".join(clauses)}
            GROUP BY invocation_id
            ORDER BY latest_timestamp DESC, latest_id DESC
            LIMIT ?
            """,
            params,
        )
        return [str(row["invocation_id"]) for row in rows]

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
        read_only: bool = False,
    ) -> None:
        super().__init__(
            sqlite_path,
            wal=wal,
            record_store=record_store,
            read_only=read_only,
        )

    def _init_schema(self) -> None:
        with self._lock:
            _create_events_schema(self._record_store)

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__


class PostgresTelemetryStore(BaseModuleStore, _TelemetryStoreMixin):
    """Postgres-backed telemetry store."""

    def __init__(self, *, record_store: RecordStore, read_only: bool = False) -> None:
        super().__init__(record_store=record_store, initialize=not read_only)

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


__all__ = [
    "PostgresTelemetryStore",
    "SQLiteTelemetryStore",
    "TelemetryEventConflictError",
]
