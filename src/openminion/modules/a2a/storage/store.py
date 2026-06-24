from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from openminion.modules.a2a.constants import A2A_IDEMPOTENCY_STATUS_IN_PROGRESS
from openminion.modules.a2a.models import (
    AgentDescriptor,
    IdempotencyRecord,
    JobRecord,
    iso_now,
)
from openminion.modules.a2a.storage.base import StateStore, idempotency_slot_is_stale
from openminion.modules.storage.record_store import RecordStore
from openminion.modules.storage.runtime.module_store import (
    BaseModuleSQLiteStore,
    BaseModuleStore,
)
from .migrations import list_migrations


def _create_state_schema(record_store: RecordStore) -> None:
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            scope TEXT NOT NULL,
            key TEXT NOT NULL,
            status TEXT NOT NULL,
            result_inline_json TEXT,
            result_ref TEXT,
            error_json TEXT,
            task_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (scope, key)
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            task_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            method TEXT NOT NULL,
            state TEXT NOT NULL,
            current_step TEXT NOT NULL,
            progress DOUBLE PRECISION NOT NULL,
            result_inline_json TEXT,
            result_ref TEXT,
            error_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL
        )
        """
    )
    record_store.execute_count(
        """
        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            capabilities_json TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state)"
    )
    record_store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at)"
    )


class _StateStoreMixin(StateStore):
    def _list_migrations(self) -> list[str]:
        return list_migrations()

    def _module_package(self) -> str:
        return __package__

    def _init_schema(self) -> None:
        with self._lock:
            _create_state_schema(self._record_store)

    def close(self) -> None:
        BaseModuleStore.close(self)

    def reserve_idempotency(
        self, key: str, scope: str, *, stale_reclaim_after_sec: int | None = None
    ) -> tuple[bool, IdempotencyRecord | None]:
        now = iso_now()
        inserted = self._record_store.execute_count(
            """
            INSERT INTO idempotency_keys(scope, key, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope, key) DO NOTHING
            """,
            (scope, key, A2A_IDEMPOTENCY_STATUS_IN_PROGRESS, now, now),
        )
        if inserted > 0:
            return True, _in_progress_idempotency(key=key, scope=scope, stamped=now)
        existing = self._get_idempotency(scope, key)
        if (
            stale_reclaim_after_sec is not None
            and existing is not None
            and existing.status == A2A_IDEMPOTENCY_STATUS_IN_PROGRESS
            and idempotency_slot_is_stale(
                existing.updated_at, stale_after_sec=stale_reclaim_after_sec
            )
        ):
            reclaimed = self._record_store.execute_count(
                """
                UPDATE idempotency_keys
                SET created_at = ?, updated_at = ?
                WHERE scope = ? AND key = ? AND status = ? AND updated_at = ?
                """,
                (
                    now,
                    now,
                    scope,
                    key,
                    A2A_IDEMPOTENCY_STATUS_IN_PROGRESS,
                    existing.updated_at,
                ),
            )
            if reclaimed > 0:
                return True, _in_progress_idempotency(key=key, scope=scope, stamped=now)
            existing = self._get_idempotency(scope, key)
        return False, existing

    def set_idempotency_result(
        self,
        key: str,
        scope: str,
        status: str,
        *,
        result_inline: dict | None = None,
        result_ref: str | None = None,
        error: dict | None = None,
        task_id: str | None = None,
    ) -> IdempotencyRecord:
        now = iso_now()
        self._record_store.execute_count(
            """
            INSERT INTO idempotency_keys(
                scope, key, status, result_inline_json, result_ref, error_json, task_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, key) DO UPDATE SET
                status=excluded.status,
                result_inline_json=excluded.result_inline_json,
                result_ref=excluded.result_ref,
                error_json=excluded.error_json,
                task_id=excluded.task_id,
                updated_at=excluded.updated_at
            """,
            (
                scope,
                key,
                status,
                _json(result_inline),
                result_ref,
                _json(error),
                task_id,
                now,
                now,
            ),
        )
        rec = self._get_idempotency(scope, key)
        if rec is None:
            raise RuntimeError(f"Failed to read idempotency record {scope}:{key}")
        return rec

    def create_job(self, job: JobRecord) -> str:
        self._record_store.execute_count(
            """
            INSERT INTO jobs(
                task_id, trace_id, idempotency_key, agent_id, method, state, current_step, progress,
                result_inline_json, result_ref, error_json, created_at, updated_at, heartbeat_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.task_id,
                job.trace_id,
                job.idempotency_key,
                job.agent_id,
                job.method,
                job.state,
                job.current_step,
                float(job.progress),
                _json(job.result_inline),
                job.result_ref,
                _json(job.error),
                job.created_at,
                job.updated_at,
                job.heartbeat_at,
            ),
        )
        return job.task_id

    def update_job(self, task_id: str, patch: dict) -> JobRecord:
        allowed = {
            "state",
            "current_step",
            "progress",
            "result_inline",
            "result_ref",
            "error",
            "updated_at",
            "heartbeat_at",
        }
        updates: dict[str, Any] = {k: v for k, v in patch.items() if k in allowed}
        if not updates:
            row = self.get_job(task_id)
            if row is None:
                raise KeyError(task_id)
            return row

        if "updated_at" not in updates:
            updates["updated_at"] = iso_now()

        assignments: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            col = {
                "result_inline": "result_inline_json",
                "error": "error_json",
            }.get(key, key)
            assignments.append(f"{col} = ?")
            if key in {"result_inline", "error"}:
                values.append(_json(value))
            else:
                values.append(value)

        values.append(task_id)
        count = self._record_store.execute_count(
            f"UPDATE jobs SET {', '.join(assignments)} WHERE task_id = ?",
            values,
        )
        if count <= 0:
            raise KeyError(task_id)
        row = self.get_job(task_id)
        if row is None:
            raise KeyError(task_id)
        return row

    def get_job(self, task_id: str) -> JobRecord | None:
        rows = self._record_store.query_dicts(
            """
            SELECT task_id, trace_id, idempotency_key, agent_id, method, state, current_step, progress,
                   result_inline_json, result_ref, error_json, created_at, updated_at, heartbeat_at
            FROM jobs
            WHERE task_id = ?
            """,
            (task_id,),
        )
        if not rows:
            return None
        return _job_from_row(rows[0])

    def list_jobs(self, filter_by: dict | None = None) -> list[JobRecord]:
        filter_by = filter_by or {}
        where: list[str] = []
        values: list[Any] = []

        state = filter_by.get("state")
        states = filter_by.get("states")
        if state:
            where.append("state = ?")
            values.append(str(state))
        if states and isinstance(states, (list, tuple, set)):
            normalized = [str(item) for item in states if str(item).strip()]
            if normalized:
                placeholders = ",".join("?" for _ in normalized)
                where.append(f"state IN ({placeholders})")
                values.extend(normalized)

        if filter_by.get("trace_id"):
            where.append("trace_id = ?")
            values.append(str(filter_by["trace_id"]))
        if filter_by.get("agent_id"):
            where.append("agent_id = ?")
            values.append(str(filter_by["agent_id"]))
        if filter_by.get("method"):
            where.append("method = ?")
            values.append(str(filter_by["method"]))

        sql = (
            "SELECT task_id, trace_id, idempotency_key, agent_id, method, state, current_step, progress, "
            "result_inline_json, result_ref, error_json, created_at, updated_at, heartbeat_at "
            "FROM jobs"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at ASC"

        limit = int(filter_by.get("limit", 500)) if filter_by else 500
        sql += " LIMIT ?"
        values.append(max(1, min(limit, 5000)))

        rows = self._record_store.query_dicts(sql, values)
        return [_job_from_row(row) for row in rows]

    def upsert_agent(self, descriptor: AgentDescriptor) -> None:
        now = iso_now()
        self._record_store.execute_count(
            """
            INSERT INTO agents(agent_id, capabilities_json, endpoint, tags_json, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                capabilities_json=excluded.capabilities_json,
                endpoint=excluded.endpoint,
                tags_json=excluded.tags_json,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                descriptor.agent_id,
                _json(descriptor.capabilities),
                descriptor.endpoint,
                _json(descriptor.tags),
                descriptor.status,
                now,
            ),
        )

    def list_agents(self) -> list[AgentDescriptor]:
        rows = self._record_store.query_dicts(
            """
            SELECT agent_id, capabilities_json, endpoint, tags_json, status
            FROM agents
            ORDER BY agent_id ASC
            """
        )
        return [_agent_from_row(row) for row in rows]

    def _get_idempotency(self, scope: str, key: str) -> IdempotencyRecord | None:
        rows = self._record_store.query_dicts(
            """
            SELECT scope, key, status, result_inline_json, result_ref, error_json, task_id, created_at, updated_at
            FROM idempotency_keys
            WHERE scope = ? AND key = ?
            """,
            (scope, key),
        )
        if not rows:
            return None
        return _idempotency_from_row(rows[0])


class SQLiteStateStore(_StateStoreMixin, BaseModuleSQLiteStore):
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        record_store: RecordStore | None = None,
        wal: bool = True,
    ) -> None:
        BaseModuleSQLiteStore.__init__(self, path, wal=wal, record_store=record_store)


class PostgresStateStore(_StateStoreMixin, BaseModuleStore):
    def __init__(self, *, record_store: RecordStore) -> None:
        BaseModuleStore.__init__(self, record_store=record_store)


def _job_from_row(row: Mapping[str, Any]) -> JobRecord:
    return JobRecord(
        task_id=str(row["task_id"]),
        trace_id=str(row["trace_id"]),
        idempotency_key=str(row["idempotency_key"]),
        agent_id=str(row["agent_id"]),
        method=str(row["method"]),
        state=str(row["state"]),
        current_step=str(row["current_step"]),
        progress=float(row["progress"]),
        result_inline=_json_load(row["result_inline_json"], None),
        result_ref=(None if row["result_ref"] is None else str(row["result_ref"])),
        error=_json_load(row["error_json"], None),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        heartbeat_at=str(row["heartbeat_at"]),
    )


def _agent_from_row(row: Mapping[str, Any]) -> AgentDescriptor:
    return AgentDescriptor(
        agent_id=str(row["agent_id"]),
        capabilities=_json_load(row["capabilities_json"], []),
        endpoint=str(row["endpoint"]),
        tags=_json_load(row["tags_json"], []),
        status=str(row["status"]),
    )


def _idempotency_from_row(row: Mapping[str, Any]) -> IdempotencyRecord:
    return IdempotencyRecord(
        key=str(row["key"]),
        scope=str(row["scope"]),
        status=str(row["status"]),
        result_inline=_json_load(row["result_inline_json"], None),
        result_ref=(None if row["result_ref"] is None else str(row["result_ref"])),
        error=_json_load(row["error_json"], None),
        task_id=(None if row["task_id"] is None else str(row["task_id"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _in_progress_idempotency(
    *, key: str, scope: str, stamped: str
) -> IdempotencyRecord:
    return IdempotencyRecord(
        key=key,
        scope=scope,
        status=A2A_IDEMPOTENCY_STATUS_IN_PROGRESS,
        created_at=stamped,
        updated_at=stamped,
    )


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True)


def _json_load(raw: Any, default: Any) -> Any:
    if raw in {None, ""}:
        return default
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return default
