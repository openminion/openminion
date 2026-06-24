from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openminion.modules.registry.models import (
    AgentDescriptor,
    AgentRecord,
    AgentStatus,
    MethodIndexRow,
    RegistrySource,
    extract_method_rows,
    iso_now,
)
from openminion.modules.registry.storage.base import RegistryStore
from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from openminion.modules.storage.record_store import RecordStore
from .migrations import list_migrations


def _create_registry_schema(record_store: RecordStore) -> None:
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            descriptor_json TEXT NOT NULL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS agent_status (
            agent_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            last_heartbeat_at TEXT,
            last_error_json TEXT,
            load_json TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS agent_methods (
            method TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            quality_tier TEXT,
            cost_tier TEXT,
            latency_hint_ms INTEGER,
            PRIMARY KEY (method, agent_id),
            FOREIGN KEY(agent_id) REFERENCES agents(agent_id) ON DELETE CASCADE
        )
        """
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_agents_source ON agents(source)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_methods_method ON agent_methods(method)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_methods_agent ON agent_methods(agent_id)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_status_state ON agent_status(state)"
    )


class _RegistryStoreMixin(RegistryStore):
    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__

    def upsert_agent(self, descriptor: AgentDescriptor, source: RegistrySource) -> None:
        now = iso_now()
        payload = json.dumps(descriptor.model_dump(mode="json"), ensure_ascii=True)
        rows = extract_method_rows(descriptor)

        with self._record_store.transaction():
            self._record_store.execute_count(
                """
                INSERT INTO agents(agent_id, descriptor_json, source, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    descriptor_json=excluded.descriptor_json,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                (descriptor.agent_id, payload, source, now),
            )
            self._record_store.delete_rows(
                "agent_methods",
                {"agent_id": descriptor.agent_id},
            )
            for row in rows:
                self._record_store.execute_count(
                    """
                    INSERT INTO agent_methods(method, agent_id, quality_tier, cost_tier, latency_hint_ms)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row.method,
                        row.agent_id,
                        row.quality_tier,
                        row.cost_tier,
                        row.latency_hint_ms,
                    ),
                )

    def delete_agent(self, agent_id: str) -> None:
        with self._record_store.transaction():
            self._record_store.delete_rows("agents", {"agent_id": agent_id})
            self._record_store.delete_rows("agent_status", {"agent_id": agent_id})

    def get_agent(self, agent_id: str) -> AgentDescriptor | None:
        rec = self.get_agent_record(agent_id)
        if rec is None:
            return None
        return rec.descriptor

    def get_agent_record(self, agent_id: str) -> AgentRecord | None:
        rows = self._record_store.query_dicts(
            """
            SELECT agent_id, descriptor_json, source, updated_at
            FROM agents
            WHERE agent_id = ?
            LIMIT 1
            """,
            (agent_id,),
        )
        if not rows:
            return None
        return _agent_record_from_row(rows[0])

    def list_agent_records(
        self, filters: dict[str, Any] | None = None
    ) -> list[AgentRecord]:
        filters = filters or {}
        where: list[str] = []
        params: list[Any] = []

        source = filters.get("source")
        if source:
            where.append("source = ?")
            params.append(str(source))

        agent_ids = filters.get("agent_ids")
        if agent_ids:
            normalized = [str(item) for item in agent_ids if str(item).strip()]
            if normalized:
                placeholders = ",".join("?" for _ in normalized)
                where.append(f"agent_id IN ({placeholders})")
                params.extend(normalized)

        sql = "SELECT agent_id, descriptor_json, source, updated_at FROM agents"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY agent_id ASC"

        rows = self._record_store.query_dicts(sql, params)
        return [_agent_record_from_row(row) for row in rows]

    def upsert_status(self, agent_id: str, status: AgentStatus) -> None:
        now = iso_now()
        error_json = _json(
            status.last_error.model_dump(mode="json") if status.last_error else None
        )
        load_json = _json(
            status.current_load.model_dump(mode="json") if status.current_load else None
        )
        self._record_store.execute_count(
            """
            INSERT INTO agent_status(agent_id, state, last_heartbeat_at, last_error_json, load_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                state=excluded.state,
                last_heartbeat_at=excluded.last_heartbeat_at,
                last_error_json=excluded.last_error_json,
                load_json=excluded.load_json,
                updated_at=excluded.updated_at
            """,
            (
                agent_id,
                status.state,
                status.last_heartbeat_at,
                error_json,
                load_json,
                now,
            ),
        )

    def get_status(self, agent_id: str) -> AgentStatus | None:
        rows = self._record_store.query_dicts(
            """
            SELECT agent_id, state, last_heartbeat_at, last_error_json, load_json
            FROM agent_status
            WHERE agent_id = ?
            LIMIT 1
            """,
            (agent_id,),
        )
        if not rows:
            return None
        return _status_from_row(rows[0])

    def list_status(self, filters: dict[str, Any] | None = None) -> list[AgentStatus]:
        filters = filters or {}
        where: list[str] = []
        params: list[Any] = []

        state = filters.get("state")
        if state:
            where.append("state = ?")
            params.append(str(state))

        agent_ids = filters.get("agent_ids")
        if agent_ids:
            normalized = [str(item) for item in agent_ids if str(item).strip()]
            if normalized:
                placeholders = ",".join("?" for _ in normalized)
                where.append(f"agent_id IN ({placeholders})")
                params.extend(normalized)

        sql = (
            "SELECT agent_id, state, last_heartbeat_at, last_error_json, load_json "
            "FROM agent_status"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY agent_id ASC"

        rows = self._record_store.query_dicts(sql, params)
        return [_status_from_row(row) for row in rows]

    def find_agent_ids_by_method(self, method: str) -> list[str]:
        rows = self._record_store.query_dicts(
            """
            SELECT agent_id
            FROM agent_methods
            WHERE method = ?
            ORDER BY agent_id ASC
            """,
            (method,),
        )
        return [str(row["agent_id"]) for row in rows]

    def get_method_rows(self, method: str) -> list[MethodIndexRow]:
        rows = self._record_store.query_dicts(
            """
            SELECT method, agent_id, quality_tier, cost_tier, latency_hint_ms
            FROM agent_methods
            WHERE method = ?
            ORDER BY agent_id ASC
            """,
            (method,),
        )
        return [_method_row_from_row(row) for row in rows]


class SQLiteRegistryStore(BaseModuleSQLiteStore, _RegistryStoreMixin):
    def __init__(
        self,
        sqlite_path: str | Path,
        *,
        record_store: RecordStore | None = None,
        wal: bool = True,
    ) -> None:
        super().__init__(sqlite_path, wal=wal, record_store=record_store)

    def _init_schema(self) -> None:
        with self._lock:
            _create_registry_schema(self._record_store)

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__


class PostgresRegistryStore(BaseModuleStore, _RegistryStoreMixin):
    def __init__(self, *, record_store: RecordStore) -> None:
        super().__init__(record_store=record_store)

    def _init_schema(self) -> None:
        with self._lock:
            _create_registry_schema(self._record_store)

    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__


def _agent_record_from_row(row: dict[str, Any]) -> AgentRecord:
    descriptor_raw = json.loads(str(row["descriptor_json"]))
    descriptor = AgentDescriptor.model_validate(descriptor_raw)
    return AgentRecord(
        agent_id=str(row["agent_id"]),
        descriptor=descriptor,
        source=str(row["source"]),
        updated_at=str(row["updated_at"]),
    )


def _status_from_row(row: dict[str, Any]) -> AgentStatus:
    payload: dict[str, Any] = {
        "agent_id": str(row["agent_id"]),
        "state": str(row["state"]),
        "last_heartbeat_at": row["last_heartbeat_at"],
        "last_error": _parse_json(row["last_error_json"]),
        "current_load": _parse_json(row["load_json"]),
    }
    return AgentStatus.model_validate(payload)


def _method_row_from_row(row: dict[str, Any]) -> MethodIndexRow:
    payload: dict[str, Any] = {
        "method": str(row["method"]),
        "agent_id": str(row["agent_id"]),
        "quality_tier": row["quality_tier"],
        "cost_tier": row["cost_tier"],
        "latency_hint_ms": row["latency_hint_ms"],
    }
    return MethodIndexRow.model_validate(payload)


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True)


def _parse_json(value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return json.loads(text)


__all__ = ["PostgresRegistryStore", "SQLiteRegistryStore"]
