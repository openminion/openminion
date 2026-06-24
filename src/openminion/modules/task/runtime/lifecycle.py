from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Protocol, runtime_checkable

from openminion.base.time import utc_now_iso as _utc_now_iso


@runtime_checkable
class TaskCronStoreProtocol(Protocol):
    def add_cron_job(self, **kwargs: Any) -> str: ...

    def delete_cron_job(self, job_id: str) -> None: ...

    def get_cron_job(self, job_id: str) -> dict[str, Any] | None: ...

    def list_cron_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def set_cron_job_enabled(self, job_id: str, enabled: bool) -> None: ...

    def list_cron_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...


class TaskLifecycleState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    DONE = "done"
    FAILED = "failed"


_TERMINAL_TASK_STATES = {
    TaskLifecycleState.CANCELLED,
    TaskLifecycleState.DONE,
    TaskLifecycleState.FAILED,
}


_ALLOWED_STATE_TRANSITIONS: dict[TaskLifecycleState, set[TaskLifecycleState]] = {
    TaskLifecycleState.ACTIVE: {
        TaskLifecycleState.PAUSED,
        TaskLifecycleState.CANCELLED,
        TaskLifecycleState.DONE,
        TaskLifecycleState.FAILED,
    },
    TaskLifecycleState.PAUSED: {
        TaskLifecycleState.ACTIVE,
        TaskLifecycleState.CANCELLED,
        TaskLifecycleState.DONE,
        TaskLifecycleState.FAILED,
    },
    TaskLifecycleState.CANCELLED: set(),
    TaskLifecycleState.DONE: set(),
    TaskLifecycleState.FAILED: set(),
}


def _normalize_task_state(value: TaskLifecycleState | str) -> TaskLifecycleState:
    if isinstance(value, TaskLifecycleState):
        return value
    normalized = str(value or "").strip().lower()
    for candidate in TaskLifecycleState:
        if normalized in {candidate.value, candidate.name.lower()}:
            return candidate
    raise ValueError(f"unknown task state: {value!r}")


def _dump_metadata(metadata: Mapping[str, Any] | None) -> str:
    return json.dumps(dict(metadata or {}), ensure_ascii=True, sort_keys=True)


def _load_metadata(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def _dump_state_blob(state: Mapping[str, Any] | None) -> str:
    return json.dumps(dict(state or {}), ensure_ascii=True, sort_keys=True)


def _load_state_blob(raw: Any) -> dict[str, Any]:
    loaded = _load_metadata(raw)
    return loaded if isinstance(loaded, dict) else {}


@dataclass(frozen=True)
class TaskLifecycleRecord:
    task_id: str
    cron_job_id: str
    agent_id: str | None
    state: TaskLifecycleState
    created_at: str
    updated_at: str
    cancelled_at: str | None
    completed_at: str | None
    failed_at: str | None
    failure_reason: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


def _new_task_id() -> str:
    return str(uuid.uuid4())


class _NullCronRepository:
    def add_cron_job(self, **kwargs: Any) -> str:
        raise NotImplementedError("Cron scheduling is unavailable for linked tasks")

    def delete_cron_job(self, job_id: str) -> None:
        del job_id

    def get_cron_job(self, job_id: str) -> dict[str, Any] | None:
        del job_id
        return None

    def list_cron_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        del limit
        return []

    def set_cron_job_enabled(self, job_id: str, enabled: bool) -> None:
        del job_id, enabled

    def list_cron_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        del job_id, limit, states
        return []


class TaskLifecycleRepository:
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

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    task_id TEXT PRIMARY KEY,
                    cron_job_id TEXT NOT NULL UNIQUE,
                    agent_id TEXT,
                    state TEXT NOT NULL
                        CHECK(state IN ('active', 'paused', 'cancelled', 'done', 'failed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    cancelled_at TEXT,
                    completed_at TEXT,
                    failed_at TEXT,
                    failure_reason TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_agent_state
                ON scheduled_tasks(agent_id, state)
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_checkpoints (
                    task_id TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY(task_id, checkpoint_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_task_checkpoints_latest
                ON task_checkpoints(task_id, created_at DESC, checkpoint_id DESC)
                """
            )
            columns = {
                str(row[1])
                for row in self._conn.execute("PRAGMA table_info(scheduled_tasks)")
            }
            if "metadata" not in columns:
                self._conn.execute(
                    """
                    ALTER TABLE scheduled_tasks
                    ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'
                    """
                )
            self._conn.commit()

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
        return self.get(record.task_id)  # type: ignore[return-value]

    def save_checkpoint(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
        state: Mapping[str, Any],
    ) -> None:
        normalized_task_id = str(task_id or "").strip()
        normalized_checkpoint_id = str(checkpoint_id or "").strip()
        if not normalized_task_id:
            raise ValueError("task_id is required")
        if not normalized_checkpoint_id:
            raise ValueError("checkpoint_id is required")
        if self.get(normalized_task_id) is None:
            raise KeyError(f"task not found: {normalized_task_id}")
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO task_checkpoints(
                    task_id,
                    checkpoint_id,
                    created_at,
                    state_json
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    normalized_task_id,
                    normalized_checkpoint_id,
                    _utc_now_iso(),
                    _dump_state_blob(state),
                ),
            )
            self._conn.commit()

    def get_latest_checkpoint(
        self, *, task_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise ValueError("task_id is required")
        with self._lock:
            row = self._conn.execute(
                """
                SELECT checkpoint_id, state_json
                FROM task_checkpoints
                WHERE task_id = ?
                ORDER BY created_at DESC, checkpoint_id DESC
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return str(row["checkpoint_id"]), _load_state_blob(row["state_json"])

    def list_checkpoints(self, *, task_id: str) -> list[str]:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise ValueError("task_id is required")
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT checkpoint_id
                FROM task_checkpoints
                WHERE task_id = ?
                ORDER BY created_at ASC, checkpoint_id ASC
                """,
                (normalized,),
            ).fetchall()
        return [str(row["checkpoint_id"]) for row in rows]

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
        return self.get(normalized_task_id)  # type: ignore[return-value]

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

    def list(self, *, limit: int = 100) -> list[TaskLifecycleRecord]:
        safe_limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM scheduled_tasks
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            record for row in rows if (record := self._row_to_record(row)) is not None
        ]

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

        if normalized_to_state not in _ALLOWED_STATE_TRANSITIONS.get(
            record.state, set()
        ):
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
            self._conn.execute(
                """
                UPDATE scheduled_tasks
                SET state = ?,
                    updated_at = ?,
                    cancelled_at = ?,
                    completed_at = ?,
                    failed_at = ?,
                    failure_reason = ?,
                    metadata = ?
                WHERE task_id = ?
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
                ),
            )
            self._conn.commit()
        return self.get(record.task_id)  # type: ignore[return-value]


class TaskManager:
    """Lifecycle layer for scheduled tasks and task-backed runtime work."""

    def __init__(
        self,
        *,
        cron_repository: TaskCronStoreProtocol,
        lifecycle_repository: TaskLifecycleRepository,
    ) -> None:
        self._cron_repository = cron_repository
        self._lifecycle_repository = lifecycle_repository

    @property
    def lifecycle_repository(self) -> TaskLifecycleRepository:
        return self._lifecycle_repository

    @classmethod
    def from_cron_repository(
        cls,
        cron_repository: TaskCronStoreProtocol,
        *,
        db_path: str | Path | None = None,
    ) -> TaskManager:
        path_hint = db_path or getattr(cron_repository, "db_path", None)
        if path_hint is None:
            path_hint = ":memory:"
        return cls(
            cron_repository=cron_repository,
            lifecycle_repository=TaskLifecycleRepository(db_path=path_hint),
        )

    @classmethod
    def for_lifecycle_db(cls, *, db_path: str | Path) -> TaskManager:
        return cls(
            cron_repository=_NullCronRepository(),  # type: ignore[arg-type]
            lifecycle_repository=TaskLifecycleRepository(db_path=db_path),
        )

    def ensure_task_record(
        self,
        *,
        cron_job_id: str,
        agent_id: str | None,
        task_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> TaskLifecycleRecord:
        effective_task_id = str(task_id or cron_job_id).strip()
        existing = self._lifecycle_repository.get(effective_task_id)
        if existing is not None:
            return existing
        return self._lifecycle_repository.create(
            task_id=effective_task_id,
            cron_job_id=str(cron_job_id).strip(),
            agent_id=agent_id,
            state=TaskLifecycleState.ACTIVE,
            metadata=metadata,
        )

    def ensure_task_record_for_job(self, job: Mapping[str, Any]) -> TaskLifecycleRecord:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("cron job payload is missing job_id")
        return self.ensure_task_record(
            cron_job_id=job_id,
            agent_id=str(job.get("agent_id") or "").strip() or None,
            task_id=job_id,
        )

    def create_linked_task(
        self,
        *,
        linked_job_id: str,
        agent_id: str | None,
        metadata: Mapping[str, Any] | None = None,
        task_id: str | None = None,
    ) -> TaskLifecycleRecord:
        normalized_job_id = str(linked_job_id or "").strip()
        if not normalized_job_id:
            raise ValueError("linked_job_id is required")
        return self._lifecycle_repository.create(
            task_id=str(task_id or _new_task_id()).strip(),
            cron_job_id=normalized_job_id,
            agent_id=agent_id,
            state=TaskLifecycleState.ACTIVE,
            metadata=metadata,
        )

    def create_task(
        self,
        *,
        session_id: str,
        mode_name: str,
        goal: str,
        agent_id: str | None,
        metadata: Mapping[str, Any] | None = None,
        task_id: str | None = None,
    ) -> TaskLifecycleRecord:
        normalized_session_id = str(session_id or "").strip()
        normalized_mode_name = str(mode_name or "").strip()
        normalized_goal = str(goal or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        if not normalized_mode_name:
            raise ValueError("mode_name is required")
        if not normalized_goal:
            raise ValueError("goal is required")
        effective_task_id = str(task_id or _new_task_id()).strip()
        merged_metadata = {
            "kind": "mode",
            "parent_session_id": normalized_session_id,
            "mode_name": normalized_mode_name,
            "goal": normalized_goal,
            "resume_count": 0,
        }
        if metadata:
            merged_metadata.update(dict(metadata))
        return self._lifecycle_repository.create(
            task_id=effective_task_id,
            cron_job_id=effective_task_id,
            agent_id=agent_id,
            state=TaskLifecycleState.ACTIVE,
            metadata=merged_metadata,
        )

    def schedule_task(
        self,
        *,
        name: str,
        schedule: Mapping[str, Any],
        payload: Mapping[str, Any],
        description: str | None = None,
        enabled: bool = True,
        agent_id: str | None = None,
        session_target: str | None = None,
        wake_mode: str | None = None,
        delivery: Mapping[str, Any] | None = None,
        delete_after_run: bool | None = None,
        misfire_policy: str | Mapping[str, Any] | None = None,
        max_lateness_s: int = 600,
        max_concurrency: int = 1,
        job_id: str | None = None,
    ) -> TaskLifecycleRecord:
        created_job_id = self._cron_repository.add_cron_job(
            name=name,
            schedule=schedule,
            payload=payload,
            description=description,
            enabled=enabled,
            agent_id=agent_id,
            session_target=session_target,
            wake_mode=wake_mode,
            delivery=delivery,
            delete_after_run=delete_after_run,
            misfire_policy=misfire_policy,
            max_lateness_s=max_lateness_s,
            max_concurrency=max_concurrency,
            job_id=job_id,
        )
        try:
            return self.ensure_task_record(
                cron_job_id=created_job_id,
                agent_id=agent_id,
                task_id=created_job_id,
            )
        except Exception:
            try:
                self._cron_repository.delete_cron_job(created_job_id)
            except Exception:
                pass
            raise

    def get_task(self, task_id: str) -> TaskLifecycleRecord | None:
        return self._lifecycle_repository.get(task_id)

    def get_task_by_job(self, cron_job_id: str) -> TaskLifecycleRecord | None:
        return self._lifecycle_repository.get_by_cron_job_id(cron_job_id)

    def list_open_tasks_for_session(
        self,
        session_id: str,
        *,
        mode_name: str | None = None,
        limit: int = 100,
    ) -> list[TaskLifecycleRecord]:
        normalized_session_id = str(session_id or "").strip()
        normalized_mode_name = str(mode_name or "").strip() or None
        if not normalized_session_id:
            raise ValueError("session_id is required")
        records = self._lifecycle_repository.list(limit=max(1, min(int(limit), 1000)))
        open_states = {TaskLifecycleState.ACTIVE, TaskLifecycleState.PAUSED}
        matched: list[TaskLifecycleRecord] = []
        for record in records:
            if record.state not in open_states:
                continue
            parent_session_id = str(
                record.metadata.get("parent_session_id")
                or record.metadata.get("session_id")
                or ""
            ).strip()
            if parent_session_id != normalized_session_id:
                continue
            record_mode_name = (
                str(record.metadata.get("mode_name") or "").strip() or None
            )
            if (
                normalized_mode_name is not None
                and record_mode_name != normalized_mode_name
            ):
                continue
            matched.append(record)
        return matched

    def get_scheduled_job(self, task_id: str) -> dict[str, Any] | None:
        return self._cron_repository.get_cron_job(task_id)

    def list_scheduled_jobs(self, *, limit: int) -> list[dict[str, Any]]:
        return self._cron_repository.list_cron_jobs(limit=limit)

    def set_scheduled_job_enabled(
        self, task_id: str, *, enabled: bool
    ) -> dict[str, Any]:
        record = self.get_task(task_id) or self.get_task_by_job(task_id)
        job = self.get_scheduled_job(task_id)
        if record is None and job is not None:
            record = self.ensure_task_record_for_job(job)
        if record is None:
            raise KeyError(f"task not found: {task_id}")
        self._cron_repository.set_cron_job_enabled(record.cron_job_id, enabled)
        refreshed = self.get_scheduled_job(record.cron_job_id)
        if refreshed is None:
            raise KeyError(f"task not found: {task_id}")
        return refreshed

    def list_scheduled_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self._cron_repository.list_cron_runs(
            job_id=job_id,
            limit=limit,
            states=states,
        )

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: TaskLifecycleState | str,
        failure_reason: str | None = None,
    ) -> TaskLifecycleRecord:
        record = self._lifecycle_repository.transition(
            task_id=task_id,
            to_state=to_state,
            failure_reason=failure_reason,
        )
        if record.state in _TERMINAL_TASK_STATES:
            self._cleanup_linked_cron_job(task_id=record.task_id)
            refreshed = self.get_task(record.task_id)
            if refreshed is not None:
                return refreshed
        return record

    def save_checkpoint(
        self,
        task_id: str,
        checkpoint_id: str,
        state: Mapping[str, Any],
    ) -> None:
        self._lifecycle_repository.save_checkpoint(
            task_id=task_id,
            checkpoint_id=checkpoint_id,
            state=state,
        )
        record = self.get_task(task_id)
        if record is None:
            raise KeyError(f"task not found: {task_id}")
        metadata = dict(record.metadata)
        metadata["last_checkpoint_id"] = str(checkpoint_id)
        self._lifecycle_repository.update_metadata(task_id=task_id, metadata=metadata)

    def get_latest_checkpoint(self, task_id: str) -> tuple[str, dict[str, Any]] | None:
        return self._lifecycle_repository.get_latest_checkpoint(task_id=task_id)

    def list_checkpoints(self, task_id: str) -> list[str]:
        return self._lifecycle_repository.list_checkpoints(task_id=task_id)

    def update_progress(self, task_id: str, progress: Mapping[str, Any]) -> None:
        record = self.get_task(task_id)
        if record is None:
            raise KeyError(f"task not found: {task_id}")
        metadata = dict(record.metadata)
        progress_payload = dict(metadata.get("progress", {}) or {})
        progress_payload.update(dict(progress))
        metadata["progress"] = progress_payload
        checkpoint_id = str(progress_payload.get("last_checkpoint_id") or "").strip()
        if checkpoint_id:
            metadata["last_checkpoint_id"] = checkpoint_id
        self._lifecycle_repository.update_metadata(task_id=task_id, metadata=metadata)

    def update_task_metadata(
        self,
        *,
        task_id: str,
        metadata: Mapping[str, Any],
    ) -> TaskLifecycleRecord:
        return self._lifecycle_repository.update_metadata(
            task_id=task_id,
            metadata=metadata,
        )

    def create_cron_job(
        self,
        *,
        name: str,
        schedule: Mapping[str, Any],
        payload: Mapping[str, Any],
        description: str | None = None,
        enabled: bool = True,
        agent_id: str | None = None,
        session_target: str | None = None,
        wake_mode: str | None = None,
        delivery: Mapping[str, Any] | None = None,
        delete_after_run: bool | None = None,
        misfire_policy: str | Mapping[str, Any] | None = None,
        max_lateness_s: int = 600,
        max_concurrency: int = 1,
        job_id: str | None = None,
    ) -> str:
        return self._cron_repository.add_cron_job(
            name=name,
            schedule=schedule,
            payload=payload,
            description=description,
            enabled=enabled,
            agent_id=agent_id,
            session_target=session_target,
            wake_mode=wake_mode,
            delivery=delivery,
            delete_after_run=delete_after_run,
            misfire_policy=misfire_policy,
            max_lateness_s=max_lateness_s,
            max_concurrency=max_concurrency,
            job_id=job_id,
        )

    def replace_cron_job_payload(
        self,
        job_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        replacer = getattr(self._cron_repository, "replace_cron_job_payload", None)
        if not callable(replacer):
            raise NotImplementedError("Cron payload replacement is unavailable")
        replacer(job_id, payload)

    def delete_scheduled_job(self, job_id: str) -> None:
        normalized = str(job_id or "").strip()
        if not normalized:
            return
        try:
            self._cron_repository.delete_cron_job(normalized)
        except Exception:
            pass

    def get_linked_cron_job(self, task_id: str) -> str | None:
        record = self.get_task(task_id)
        if record is None:
            return None
        linked = str(record.metadata.get("linked_cron_job_id") or "").strip()
        return linked or None

    def find_task_by_linked_cron_job(
        self, cron_job_id: str
    ) -> TaskLifecycleRecord | None:
        normalized = str(cron_job_id or "").strip()
        if not normalized:
            return None
        for record in self._lifecycle_repository.list(limit=1000):
            linked = str(record.metadata.get("linked_cron_job_id") or "").strip()
            if linked == normalized:
                return record
        return None

    def cancel_task(self, task_id: str) -> TaskLifecycleRecord:
        record = self.get_task(task_id) or self.get_task_by_job(task_id)
        if record is None:
            job = self.get_scheduled_job(task_id)
            if job is None:
                raise KeyError(f"task not found: {task_id}")
            record = self.ensure_task_record_for_job(job)
        self._cron_repository.delete_cron_job(record.cron_job_id)
        return self.transition_task(
            task_id=record.task_id,
            to_state=TaskLifecycleState.CANCELLED,
        )

    def pause_task(self, task_id: str) -> tuple[TaskLifecycleRecord, dict[str, Any]]:
        record = self.get_task(task_id) or self.get_task_by_job(task_id)
        job = self.get_scheduled_job(task_id)
        if record is None and job is not None:
            record = self.ensure_task_record_for_job(job)
        if record is None:
            raise KeyError(f"task not found: {task_id}")
        self._cron_repository.set_cron_job_enabled(record.cron_job_id, False)
        record = self.transition_task(
            task_id=record.task_id,
            to_state=TaskLifecycleState.PAUSED,
        )
        refreshed = self.get_scheduled_job(record.cron_job_id)
        if refreshed is None:
            raise KeyError(f"task not found: {task_id}")
        return record, refreshed

    def resume_task(self, task_id: str) -> tuple[TaskLifecycleRecord, dict[str, Any]]:
        record = self.get_task(task_id) or self.get_task_by_job(task_id)
        job = self.get_scheduled_job(task_id)
        if record is None and job is not None:
            record = self.ensure_task_record_for_job(job)
        if record is None:
            raise KeyError(f"task not found: {task_id}")
        self._cron_repository.set_cron_job_enabled(record.cron_job_id, True)
        record = self.transition_task(
            task_id=record.task_id,
            to_state=TaskLifecycleState.ACTIVE,
        )
        refreshed = self.get_scheduled_job(record.cron_job_id)
        if refreshed is None:
            raise KeyError(f"task not found: {task_id}")
        return record, refreshed

    def _cleanup_linked_cron_job(self, *, task_id: str) -> None:
        record = self.get_task(task_id)
        if record is None:
            return
        metadata = dict(record.metadata)
        linked_cron_job_id = str(metadata.get("linked_cron_job_id") or "").strip()
        if not linked_cron_job_id:
            return
        try:
            self.delete_scheduled_job(linked_cron_job_id)
        finally:
            metadata.pop("linked_cron_job_id", None)
            self.update_task_metadata(task_id=task_id, metadata=metadata)


__all__ = [
    "TaskCronStoreProtocol",
    "TaskLifecycleRecord",
    "TaskLifecycleRepository",
    "TaskLifecycleState",
    "TaskManager",
]
