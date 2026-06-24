from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openminion.modules.storage.record_store import RecordStore

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_postgres_backend(store: RecordStore) -> bool:
    diagnostics = store.diagnostics()
    return str(diagnostics.get("backend", "")).strip().lower() == "postgres"


def _assert_identifier(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name


def migrate_v1_to_v2(store: RecordStore) -> None:
    """Add task-related tables."""
    store.execute_count("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL CHECK(status IN ('PENDING', 'ACTIVE', 'WAITING', 'DONE', 'CANCELED')),
            due_at TEXT,
            scheduled_at TEXT,
            wait_at TEXT,
            created_by_mode TEXT,
            executing_mode TEXT,
            current_plan_id TEXT,
            next_step_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id)
        );
    """)

    store.execute_count("""
        CREATE TABLE IF NOT EXISTS plans (
            plan_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            plan_name TEXT,
            created_by_mode TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(task_id),
            UNIQUE(plan_id)
        );
    """)

    store.execute_count("""
        CREATE TABLE IF NOT EXISTS plan_steps (
            step_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            title TEXT NOT NULL,
            instruction TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('PENDING', 'ACTIVE', 'DONE', 'FAILED', 'BLOCKED')),
            note TEXT,
            artifact_refs TEXT NOT NULL DEFAULT '[]',
            executing_mode TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(plan_id) REFERENCES plans(plan_id),
            UNIQUE(step_id),
            UNIQUE(plan_id, order_index)
        );
    """)

    store.execute_count("""
        CREATE TABLE IF NOT EXISTS pending_actions (
            pending_action_id TEXT PRIMARY KEY,
            policy_request_id TEXT UNIQUE NOT NULL,
            state TEXT NOT NULL CHECK(state = 'NEEDS_APPROVAL'),
            reason TEXT,
            task_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 1,
            trace_id TEXT NOT NULL,
            turn_id TEXT,
            pack_id TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            decision_id TEXT,
            FOREIGN KEY(task_id) REFERENCES tasks(task_id),
            FOREIGN KEY(plan_id) REFERENCES plans(plan_id),
            UNIQUE(task_id, plan_id, step_id, attempt)
        );
    """)

    store.execute_count("""
        CREATE TABLE IF NOT EXISTS step_idempotency (
            idempotency_key TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('PENDING', 'ACTIVE', 'DONE', 'FAILED', 'BLOCKED')),
            note TEXT,
            artifact_refs TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)

    store.execute_count("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")
    store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_tasks_current_plan ON tasks(current_plan_id);"
    )
    store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_plans_task_id ON plans(task_id);"
    )
    store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_plan_steps_plan_id ON plan_steps(plan_id);"
    )
    store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_plan_steps_status ON plan_steps(status);"
    )
    store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_pending_actions_state ON pending_actions(state);"
    )
    store.execute_count(
        "CREATE INDEX IF NOT EXISTS idx_pending_policy_request_id ON pending_actions(policy_request_id);"
    )

    _ensure_optional_column(
        store, table="tasks", column="created_by_mode", column_sql="TEXT"
    )
    _ensure_optional_column(
        store, table="tasks", column="executing_mode", column_sql="TEXT"
    )
    _ensure_optional_column(
        store, table="plans", column="created_by_mode", column_sql="TEXT"
    )
    _ensure_optional_column(
        store, table="plans", column="root_goal_id", column_sql="TEXT"
    )
    _ensure_optional_column(
        store, table="plan_steps", column="executing_mode", column_sql="TEXT"
    )


def migrate_v2_to_v3(store: RecordStore) -> None:
    del store


def _ensure_optional_column(
    store: RecordStore,
    *,
    table: str,
    column: str,
    column_sql: str,
) -> None:
    if _table_has_column(store, table=table, column=column):
        return
    store.execute_count(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")


def _table_has_column(store: RecordStore, *, table: str, column: str) -> bool:
    table_name = _assert_identifier(table)
    column_name = _assert_identifier(column)
    if _is_postgres_backend(store):
        rows = store.query_dicts(
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ?
              AND column_name = ?
            LIMIT 1
            """,
            (table_name, column_name),
        )
        return bool(rows)

    rows = store.query_dicts(f"PRAGMA table_info({table_name})")
    return any(str(row["name"]).strip() == column_name for row in rows)
