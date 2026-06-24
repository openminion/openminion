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
        descriptor_json TEXT NOT NULL,
        source TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_status (
        agent_id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        last_heartbeat_at TEXT,
        last_error_json TEXT,
        load_json TEXT,
        updated_at TEXT NOT NULL
    )
    """,
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
    """,
    """
    CREATE TABLE IF NOT EXISTS om_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agents_source ON agents(source)",
    "CREATE INDEX IF NOT EXISTS idx_methods_agent ON agent_methods(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_methods_method ON agent_methods(method)",
    "CREATE INDEX IF NOT EXISTS idx_status_state ON agent_status(state)",
)


def upgrade() -> None:
    apply_ddl_statements(DDL)


def downgrade() -> None:
    drop_sql_objects(
        table_names=("agents", "agent_status", "agent_methods", "om_meta"),
        index_names=(
            "idx_agents_source",
            "idx_methods_agent",
            "idx_methods_method",
            "idx_status_state",
        ),
    )
