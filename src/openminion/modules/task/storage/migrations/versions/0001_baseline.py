from openminion.modules.storage.migrations.alembic import (
    apply_ddl_statements,
    drop_sql_objects,
)


revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

DDL = (
    """
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS plans (
        plan_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        plan_name TEXT,
        created_by_mode TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(task_id),
        UNIQUE(plan_id)
    )
    """,
    """
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
    )
    """,
    """
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS step_idempotency (
        idempotency_key TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('PENDING', 'ACTIVE', 'DONE', 'FAILED', 'BLOCKED')),
        note TEXT,
        artifact_refs TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pending_actions_state ON pending_actions(state)",
    "CREATE INDEX IF NOT EXISTS idx_pending_policy_request_id ON pending_actions(policy_request_id)",
    "CREATE INDEX IF NOT EXISTS idx_plan_steps_plan_id ON plan_steps(plan_id)",
    "CREATE INDEX IF NOT EXISTS idx_plan_steps_status ON plan_steps(status)",
    "CREATE INDEX IF NOT EXISTS idx_plans_task_id ON plans(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_current_plan ON tasks(current_plan_id)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=(
            "tasks",
            "plans",
            "plan_steps",
            "pending_actions",
            "step_idempotency",
            "om_meta",
        ),
        index_names=(
            "idx_pending_actions_state",
            "idx_pending_policy_request_id",
            "idx_plan_steps_plan_id",
            "idx_plan_steps_status",
            "idx_plans_task_id",
            "idx_tasks_current_plan",
            "idx_tasks_status",
        ),
    )
