# mypy: ignore-errors
from __future__ import annotations


class TaskLifecycleRepositorySchemaMixin:
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
