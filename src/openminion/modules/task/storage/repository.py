import json
from datetime import datetime
from typing import Any
from collections.abc import Mapping

from openminion.modules.storage.record_store import RecordStore
from .store import ensure_schema
from ..schemas import (
    PlanStepStatus,
    ResumePointer,
    TaskStatus,
)


class SqlTaskRepository:
    """SQL-based persistent storage for task, plan, and step records."""

    def __init__(self, store: RecordStore):
        self._store = store
        self.initialize_schema()

    def initialize_schema(self) -> None:
        """Initialize the database schema for task persistence."""
        ensure_schema(self._store)

    def _query_one(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> Mapping[str, Any] | None:
        rows = self._store.query_dicts(query, params)
        return rows[0] if rows else None

    @staticmethod
    def _json_refs(artifact_refs: list[str]) -> str:
        return json.dumps(artifact_refs)

    def _get_tasks(
        self,
        where_clause: str,
        *,
        limit: int,
    ) -> list[Mapping[str, Any]]:
        return self._store.query_dicts(
            f"""
            SELECT * FROM tasks
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )

    def create_task(
        self,
        task_id: str,
        title: str,
        description: str | None,
        status: TaskStatus,
        due_at: datetime | None,
        scheduled_at: datetime | None,
        wait_at: datetime | None,
        created_by_mode: str | None,
        executing_mode: str | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        """Insert a new task record."""
        self._store.execute_count(
            """
            INSERT INTO tasks
            (task_id, title, description, status, due_at, scheduled_at, wait_at, created_by_mode, executing_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                title,
                description,
                status.value,
                due_at.isoformat() if due_at else None,
                scheduled_at.isoformat() if scheduled_at else None,
                wait_at.isoformat() if wait_at else None,
                created_by_mode,
                executing_mode,
                created_at.isoformat(),
                updated_at.isoformat(),
            ),
        )

    def update_task(
        self,
        task_id: str,
        status: TaskStatus | None = None,
        current_plan_id: str | None = None,
        next_step_id: str | None = None,
        executing_mode: str | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        """Update an existing task record."""
        parts = []
        params = []

        if status is not None:
            parts.append("status = ?")
            params.append(status.value)
        if current_plan_id is not None:
            parts.append("current_plan_id = ?")
            params.append(current_plan_id)
        if next_step_id is not None:
            parts.append("next_step_id = ?")
            params.append(next_step_id)
        if executing_mode is not None:
            parts.append("executing_mode = ?")
            params.append(executing_mode)
        if updated_at is not None:
            parts.append("updated_at = ?")
            params.append(updated_at.isoformat())
        else:
            parts.append("updated_at = CURRENT_TIMESTAMP")

        updates_sql = ", ".join(parts)
        params.append(task_id)  # This is the WHERE value

        self._store.execute_count(
            f"UPDATE tasks SET {updates_sql} WHERE task_id = ?",
            params,
        )

    def get_task(self, task_id: str) -> Mapping[str, Any] | None:
        """Retrieve a task record by ID."""
        return self._query_one(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        )

    def create_plan(
        self,
        plan_id: str,
        task_id: str,
        plan_name: str | None,
        root_goal_id: str | None,
        created_by_mode: str | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        """Insert a new plan record."""
        self._store.execute_count(
            """
            INSERT INTO plans
            (plan_id, task_id, plan_name, root_goal_id, created_by_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                task_id,
                plan_name,
                root_goal_id,
                created_by_mode,
                created_at.isoformat(),
                updated_at.isoformat(),
            ),
        )

    def update_plan(self, plan_id: str, updated_at: datetime) -> None:
        """Update a plan's timestamp."""
        self._store.execute_count(
            "UPDATE plans SET updated_at = ? WHERE plan_id = ?",
            (updated_at.isoformat(), plan_id),
        )

    def get_plan(self, plan_id: str) -> Mapping[str, Any] | None:
        """Retrieve a plan record by ID."""
        return self._query_one(
            "SELECT * FROM plans WHERE plan_id = ?",
            (plan_id,),
        )

    def attach_plan_to_task(self, task_id: str, plan_id: str) -> None:
        """Link a plan to a task."""
        self._store.execute_count(
            "UPDATE tasks SET current_plan_id = ? WHERE task_id = ?", (plan_id, task_id)
        )

    def create_step(
        self,
        step_id: str,
        plan_id: str,
        order_index: int,
        title: str,
        instruction: str,
        status: PlanStepStatus,
        note: str | None,
        artifact_refs: list[str],
        executing_mode: str | None,
        updated_at: datetime,
    ) -> None:
        """Insert a new plan step."""
        self._store.execute_count(
            """
            INSERT INTO plan_steps
            (step_id, plan_id, order_index, title, instruction, status, note, artifact_refs, executing_mode, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                step_id,
                plan_id,
                order_index,
                title,
                instruction,
                status.value,
                note,
                self._json_refs(artifact_refs),
                executing_mode,
                updated_at.isoformat(),
            ),
        )

    def update_step(
        self,
        step_id: str,
        status: PlanStepStatus | None = None,
        note: str | None = None,
        artifact_refs: list[str] | None = None,
        executing_mode: str | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        """Update an existing plan step."""
        parts = []
        params = []

        if status is not None:
            parts.append("status = ?")
            params.append(status.value)
        if note is not None:
            parts.append("note = ?")
            params.append(note)
        if artifact_refs is not None:
            parts.append("artifact_refs = ?")
            params.append(self._json_refs(artifact_refs))
        if executing_mode is not None:
            parts.append("executing_mode = ?")
            params.append(executing_mode)
        if updated_at is not None:
            parts.append("updated_at = ?")
            params.append(updated_at.isoformat())
        else:
            parts.append("updated_at = CURRENT_TIMESTAMP")

        params.append(step_id)  # This is the WHERE value

        updates_sql = ", ".join(parts)

        self._store.execute_count(
            f"UPDATE plan_steps SET {updates_sql} WHERE step_id = ?", params
        )

    def get_step(self, step_id: str) -> Mapping[str, Any] | None:
        """Retrieve a plan step by ID."""
        return self._query_one("SELECT * FROM plan_steps WHERE step_id = ?", (step_id,))

    def get_steps_for_plan(self, plan_id: str) -> list[Mapping[str, Any]]:
        """Fetch all steps for a given plan ordered by index."""
        rows = self._store.query_dicts(
            "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY order_index ASC",
            (plan_id,),
        )
        return rows

    def record_pending_action(
        self,
        pending_action_id: str,
        policy_request_id: str,
        state: str,
        reason: str | None,
        cursor: ResumePointer,
        created_at: datetime,
    ) -> None:
        """Record a pending action for approval."""
        self._store.execute_count(
            """
            INSERT INTO pending_actions
            (pending_action_id, policy_request_id, state, reason,
             task_id, plan_id, step_id, attempt, trace_id, turn_id, pack_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pending_action_id,
                policy_request_id,
                state,
                reason,
                cursor.task_id,
                cursor.plan_id,
                cursor.step_id,
                cursor.attempt,
                cursor.trace_id,
                cursor.turn_id,
                cursor.pack_id,
                created_at.isoformat(),
            ),
        )

    def get_pending_action(self, policy_request_id: str) -> Mapping[str, Any] | None:
        """Get a pending action by policy request ID."""
        return self._query_one(
            "SELECT * FROM pending_actions WHERE policy_request_id = ?",
            (policy_request_id,),
        )

    def count_pending_actions(self) -> int:
        """Count unresolved approval checkpoints."""
        rows = self._store.query_dicts(
            "SELECT COUNT(*) AS count FROM pending_actions WHERE resolved_at IS NULL"
        )
        if not rows:
            return 0
        return int(rows[0].get("count") or 0)

    def update_pending_action(
        self,
        policy_request_id: str,
        resolved_at: datetime | None,
        decision_id: str | None = None,
    ) -> None:
        """Mark a pending action as resolved."""
        query = """
            UPDATE pending_actions
            SET decision_id = ?
            WHERE policy_request_id = ?
            """
        params: tuple[object, ...] = (decision_id, policy_request_id)
        if resolved_at:
            query = """
                UPDATE pending_actions
                SET resolved_at = ?, decision_id = ?
                WHERE policy_request_id = ?
                """
            params = (resolved_at.isoformat(), decision_id, policy_request_id)
        self._store.execute_count(query, params)

    def record_idempotency(
        self,
        idempotency_key: str,
        task_id: str,
        step_id: str,
        status: PlanStepStatus,
        note: str | None,
        artifact_refs: list[str],
    ) -> None:
        """Record an idempotency key to prevent duplicate operations."""
        self._store.execute_count(
            """
            INSERT INTO step_idempotency
            (idempotency_key, task_id, step_id, status, note, artifact_refs)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                idempotency_key,
                task_id,
                step_id,
                status.value,
                note,
                self._json_refs(artifact_refs),
            ),
        )

    def get_idempotency_record(self, idempotency_key: str) -> Mapping[str, Any] | None:
        """Retrieve an idempotency record."""
        return self._query_one(
            "SELECT * FROM step_idempotency WHERE idempotency_key = ?",
            (idempotency_key,),
        )

    def get_tasks_ready(self, limit: int = 5) -> list[Mapping[str, Any]]:
        """Get ready tasks (PENDING or ACTIVE)."""
        return self._get_tasks("status IN ('PENDING', 'ACTIVE')", limit=limit)

    def get_tasks_active(self, limit: int = 5) -> list[Mapping[str, Any]]:
        """Get active tasks."""
        return self._get_tasks("status = 'ACTIVE'", limit=limit)

    def get_tasks_waiting(self, limit: int = 5) -> list[Mapping[str, Any]]:
        """Get waiting tasks."""
        return self._get_tasks("status = 'WAITING'", limit=limit)
