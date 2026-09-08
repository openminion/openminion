from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import RLock
from typing import Any

from openminion.base.time import utc_now_iso as _utc_now_iso

from .lifecycle_models import (
    _ALLOWED_STATE_TRANSITIONS,
    _dump_metadata,
    _load_metadata,
    _normalize_task_state,
    TaskLifecycleRecord,
    TaskLifecycleState,
)
from .lifecycle_checkpoints import TaskLifecycleRepositoryCheckpointMixin
from .lifecycle_schema import TaskLifecycleRepositorySchemaMixin


def _require_task_record(
    record: TaskLifecycleRecord | None, *, task_id: str
) -> TaskLifecycleRecord:
    if record is None:
        raise KeyError(f"task not found after write: {task_id}")
    return record


class TaskLifecycleRepository(
    TaskLifecycleRepositoryCheckpointMixin,
    TaskLifecycleRepositorySchemaMixin,
):
    """Durable lifecycle storage for task-tool scheduled tasks."""

    def __init__(self, *, db_path: str | Path) -> None:
        raw_target = str(db_path)
        if raw_target.strip() == ":memory:":
            self.db_path = Path(":memory:")
            sqlite_target = ":memory:"
        else:
            self.db_path = Path(db_path).expanduser().resolve(strict=False)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            sqlite_target = str(self.db_path)
        self._lock = RLock()
        self._conn = sqlite3.connect(sqlite_target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def update_metadata(
        self, *, task_id: str, metadata: Mapping[str, Any]
    ) -> TaskLifecycleRecord:
        record = self.get(task_id)
        if record is None:
            raise KeyError(f"task not found: {task_id}")
        with self._lock:
            self._conn.execute(
                """
                UPDATE scheduled_tasks
                SET metadata = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    _dump_metadata(metadata),
                    _utc_now_iso(),
                    record.task_id,
                ),
            )
            self._conn.commit()
        return _require_task_record(self.get(record.task_id), task_id=record.task_id)

    def _row_to_record(self, row: sqlite3.Row | None) -> TaskLifecycleRecord | None:
        if row is None:
            return None
        return TaskLifecycleRecord(
            task_id=str(row["task_id"]),
            cron_job_id=str(row["cron_job_id"]),
            agent_id=str(row["agent_id"]) if row["agent_id"] is not None else None,
            state=TaskLifecycleState(str(row["state"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            cancelled_at=str(row["cancelled_at"])
            if row["cancelled_at"] is not None
            else None,
            completed_at=str(row["completed_at"])
            if row["completed_at"] is not None
            else None,
            failed_at=str(row["failed_at"]) if row["failed_at"] is not None else None,
            failure_reason=str(row["failure_reason"])
            if row["failure_reason"] is not None
            else None,
            metadata=_load_metadata(row["metadata"]),
        )

    def create(
        self,
        *,
        task_id: str,
        cron_job_id: str,
        agent_id: str | None,
        state: TaskLifecycleState = TaskLifecycleState.ACTIVE,
        metadata: Mapping[str, Any] | None = None,
    ) -> TaskLifecycleRecord:
        normalized_task_id = str(task_id or "").strip()
        normalized_job_id = str(cron_job_id or "").strip()
        if not normalized_task_id:
            raise ValueError("task_id is required")
        if not normalized_job_id:
            raise ValueError("cron_job_id is required")

        now = _utc_now_iso()
        cancelled_at = now if state == TaskLifecycleState.CANCELLED else None
        completed_at = now if state == TaskLifecycleState.DONE else None
        failed_at = now if state == TaskLifecycleState.FAILED else None

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO scheduled_tasks(
                    task_id,
                    cron_job_id,
                    agent_id,
                    state,
                    created_at,
                    updated_at,
                    cancelled_at,
                    completed_at,
                    failed_at,
                    failure_reason,
                    metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_task_id,
                    normalized_job_id,
                    str(agent_id).strip() if agent_id else None,
                    state.value,
                    now,
                    now,
                    cancelled_at,
                    completed_at,
                    failed_at,
                    None,
                    _dump_metadata(metadata),
                ),
            )
            self._conn.commit()
        return _require_task_record(
            self.get(normalized_task_id), task_id=normalized_task_id
        )

    def get(self, task_id: str) -> TaskLifecycleRecord | None:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise ValueError("task_id is required")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scheduled_tasks WHERE task_id = ?",
                (normalized,),
            ).fetchone()
        return self._row_to_record(row)

    def get_by_cron_job_id(self, cron_job_id: str) -> TaskLifecycleRecord | None:
        normalized = str(cron_job_id or "").strip()
        if not normalized:
            raise ValueError("cron_job_id is required")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scheduled_tasks WHERE cron_job_id = ?",
                (normalized,),
            ).fetchone()
        return self._row_to_record(row)

    def list(
        self, *, limit: int = 100, agent_id: str | None = None
    ) -> list[TaskLifecycleRecord]:
        safe_limit = max(1, min(int(limit), 1000))
        normalized_agent_id = str(agent_id or "").strip()
        where_sql = "WHERE agent_id = ?" if normalized_agent_id else ""
        params: tuple[Any, ...] = (
            (normalized_agent_id, safe_limit) if normalized_agent_id else (safe_limit,)
        )
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT *
                FROM scheduled_tasks
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            record for row in rows if (record := self._row_to_record(row)) is not None
        ]

    def list_reconciliation_candidates(
        self,
        *,
        limit: int,
        after: tuple[str, str] | None = None,
    ) -> Sequence[TaskLifecycleRecord]:
        safe_limit = max(1, min(int(limit), 1000))
        where_sql = "WHERE state IN ('active', 'paused')"
        params: tuple[Any, ...] = (safe_limit,)
        if after is not None:
            where_sql += " AND (created_at > ? OR (created_at = ? AND task_id > ?))"
            params = (after[0], after[0], after[1], safe_limit)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM scheduled_tasks
                {where_sql}
                ORDER BY created_at ASC, task_id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            record for row in rows if (record := self._row_to_record(row)) is not None
        ]

    def cancel_scheduled_task(
        self,
        *,
        task_id: str,
        expected_state: TaskLifecycleState,
    ) -> TaskLifecycleRecord:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise ValueError("task_id is required")
        now = _utc_now_iso()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM scheduled_tasks WHERE task_id = ?",
                (normalized,),
            ).fetchone()
            record = self._row_to_record(row)
            if record is None:
                raise KeyError(f"task not found: {task_id}")
            if record.state == TaskLifecycleState.CANCELLED:
                return record
            if expected_state not in {
                TaskLifecycleState.ACTIVE,
                TaskLifecycleState.PAUSED,
            }:
                raise ValueError(
                    "invalid task state transition: "
                    f"{expected_state.value} -> cancelled"
                )
            updated = self._conn.execute(
                """
                UPDATE scheduled_tasks
                SET state = 'cancelled', updated_at = ?, cancelled_at = ?
                WHERE task_id = ? AND state IN (?, 'done', 'failed')
                """,
                (
                    now,
                    record.cancelled_at or now,
                    record.task_id,
                    expected_state.value,
                ),
            ).rowcount
            if updated != 1:
                current = _require_task_record(
                    self.get(record.task_id), task_id=record.task_id
                )
                raise ValueError(
                    f"invalid task state transition: {current.state.value} -> cancelled"
                )
        return _require_task_record(self.get(record.task_id), task_id=record.task_id)

    def transition(
        self,
        *,
        task_id: str,
        to_state: TaskLifecycleState | str,
        failure_reason: str | None = None,
    ) -> TaskLifecycleRecord:
        normalized_to_state = _normalize_task_state(to_state)
        record = self.get(task_id)
        if record is None:
            raise KeyError(f"task not found: {task_id}")
        if record.state == normalized_to_state:
            return record

        if normalized_to_state not in _ALLOWED_STATE_TRANSITIONS[record.state]:
            raise ValueError(
                "invalid task state transition: "
                f"{record.state.value} -> {normalized_to_state.value}"
            )

        now = _utc_now_iso()
        cancelled_at = record.cancelled_at
        completed_at = record.completed_at
        failed_at = record.failed_at
        persisted_reason = record.failure_reason

        if normalized_to_state == TaskLifecycleState.CANCELLED and cancelled_at is None:
            cancelled_at = now
        if normalized_to_state == TaskLifecycleState.DONE and completed_at is None:
            completed_at = now
        if normalized_to_state == TaskLifecycleState.FAILED:
            failed_at = now
            persisted_reason = str(failure_reason or "").strip() or "failed"

        with self._lock:
            updated = self._conn.execute(
                """
                UPDATE scheduled_tasks
                SET state = ?,
                    updated_at = ?,
                    cancelled_at = ?,
                    completed_at = ?,
                    failed_at = ?,
                    failure_reason = ?,
                    metadata = ?
                WHERE task_id = ? AND state = ?
                """,
                (
                    normalized_to_state.value,
                    now,
                    cancelled_at,
                    completed_at,
                    failed_at,
                    persisted_reason,
                    _dump_metadata(record.metadata),
                    record.task_id,
                    record.state.value,
                ),
            ).rowcount
            self._conn.commit()
        if updated != 1:
            current = _require_task_record(
                self.get(record.task_id),
                task_id=record.task_id,
            )
            raise ValueError(
                "invalid task state transition: "
                f"{current.state.value} -> {normalized_to_state.value}"
            )
        return _require_task_record(self.get(record.task_id), task_id=record.task_id)

    def record_scheduled_outcome(
        self,
        *,
        task_id: str,
        expected_state: TaskLifecycleState,
        to_state: TaskLifecycleState,
        metadata: Mapping[str, Any],
        failure_reason: str | None = None,
    ) -> TaskLifecycleRecord:
        record = self.get(task_id)
        if record is None:
            raise KeyError(f"task not found: {task_id}")
        if record.state != expected_state:
            return record
        if (
            to_state != expected_state
            and to_state not in _ALLOWED_STATE_TRANSITIONS[expected_state]
        ):
            raise ValueError(
                "invalid task state transition: "
                f"{expected_state.value} -> {to_state.value}"
            )

        now = _utc_now_iso()
        completed_at = record.completed_at
        failed_at = record.failed_at
        persisted_reason = record.failure_reason
        if to_state == TaskLifecycleState.DONE and completed_at is None:
            completed_at = now
        if to_state == TaskLifecycleState.FAILED:
            failed_at = now
            persisted_reason = str(failure_reason or "").strip() or "failed"

        with self._lock:
            self._conn.execute(
                """
                UPDATE scheduled_tasks
                SET state = ?, updated_at = ?, completed_at = ?, failed_at = ?,
                    failure_reason = ?, metadata = ?
                WHERE task_id = ? AND state = ?
                """,
                (
                    to_state.value,
                    now,
                    completed_at,
                    failed_at,
                    persisted_reason,
                    _dump_metadata(metadata),
                    record.task_id,
                    expected_state.value,
                ),
            )
            self._conn.commit()
        return _require_task_record(self.get(record.task_id), task_id=record.task_id)


__all__ = ["TaskLifecycleRepository"]
