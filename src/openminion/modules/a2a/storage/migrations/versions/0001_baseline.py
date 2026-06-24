"""Modules a2a storage migrations versions 0001 baseline."""

from __future__ import annotations

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
    CREATE TABLE IF NOT EXISTS agents (
        agent_id TEXT PRIMARY KEY,
        capabilities_json TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        tags_json TEXT NOT NULL,
        status TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
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
    """,
    """
    CREATE TABLE IF NOT EXISTS jobs (
        task_id TEXT PRIMARY KEY,
        trace_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        method TEXT NOT NULL,
        state TEXT NOT NULL,
        current_step TEXT NOT NULL,
        progress REAL NOT NULL,
        result_inline_json TEXT,
        result_ref TEXT,
        error_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at)",
)

POSTGRES_DDL = (
    """
    CREATE TABLE IF NOT EXISTS agents (
        agent_id TEXT PRIMARY KEY,
        capabilities_json TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        tags_json TEXT NOT NULL,
        status TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
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
    """,
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
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at)",
)


def upgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    apply_ddl_statements(POSTGRES_DDL if bind.dialect.name == "postgresql" else DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=("agents", "idempotency_keys", "jobs", "om_meta"),
        index_names=("idx_jobs_state", "idx_jobs_updated"),
    )
